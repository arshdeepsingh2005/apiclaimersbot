"""
Slot-purchase payment glue (OxaPay → allocate a slot).

Buy flow:
  1. Mini App: order/begin (backend) → {order_id, price_usd}
  2. Mini App: order/pay → create_invoice_for_order() → OxaPay pay_url
  3. Buyer pays → OxaPay webhook (HMAC-verified in app.py) → enqueue_allocate()
  4. This worker re-verifies the invoice with OxaPay, then calls the backend's
     /api/cust/order/allocate (idempotent + payment-integrity gated) which moves
     the encrypted pending token onto a real slot and pushes it to the operator.

Mirrors bot/payments.py's single-worker queue pattern (workers=1 → shared
in-process state). The backend re-verifies amount/currency/status against the
stored order, so allocation is safe even if this bot is generous.
"""
import logging
import os
import queue
import threading
import time

from bot import apiclaimer_client, oxapay

logger = logging.getLogger(__name__)

_QUEUE_MAX = int(os.environ.get("ALLOCATE_QUEUE_MAX", "2000"))
_alloc_queue: "queue.Queue[tuple[str, str]]" = queue.Queue(maxsize=_QUEUE_MAX)

_seen_lock = threading.Lock()
_recent: "dict[str, float]" = {}
_RECENT_TTL = 3.0

_started = False
_start_lock = threading.Lock()


def create_invoice_for_order(order_id: str, price_usd: float, description: str = "") -> dict:
    """Create an OxaPay invoice for an existing backend order. Returns
    {ok, pay_url, track_id} (never raises)."""
    return oxapay.create_invoice(float(price_usd), order_id,
                                 description or f"API-Claimer slot ({order_id[:8]})")


def create_invoice_for_cart(cart_id: str, total_usd: float, description: str = "") -> dict:
    """ONE OxaPay invoice for a whole multi-slot cart. The OxaPay reference is the
    cart_id — the webhook forwards it and the backend allocates EVERY order in the
    cart under one total-based payment check. Returns {ok, pay_url, track_id}."""
    return oxapay.create_invoice(float(total_usd), cart_id,
                                 description or f"API-Claimer cart ({cart_id[:8]})")


def enqueue_allocate(order_id: str, track_id: str = "") -> str:
    """Called by the HMAC-verified webhook. Non-blocking; de-dupes; never raises."""
    order_id = (order_id or "").strip()
    if not order_id:
        return "invalid"
    track_id = (track_id or "").strip()
    now = time.time()
    with _seen_lock:
        if len(_recent) > 4096:
            for k in [k for k, t in _recent.items() if now - t > _RECENT_TTL]:
                _recent.pop(k, None)
        last = _recent.get(order_id)
        if last is not None and now - last < _RECENT_TTL:
            return "coalesced"
        _recent[order_id] = now
    try:
        _alloc_queue.put_nowait((order_id, track_id))
        logger.info(f"ALLOC_ENQUEUE_OK | order={order_id} track={track_id or '?'} "
                    f"qsize={_alloc_queue.qsize()}")
        return "enqueued"
    except queue.Full:
        logger.warning(f"ALLOC_ENQUEUE_FULL | order={order_id}")
        return "full"


def _call_allocate(order_id, amount=None, currency=None, track_id=None, status=None) -> str:
    """POST allocate and classify by HTTP status → 'terminal' | 'transient'.
    Uses order_allocate_full so we can read the backend's error code (a plain
    non-200 loses the body). Alerts the operator on hard failures of a PAID order."""
    http, body = apiclaimer_client.order_allocate_full(
        order_id, paid_amount=amount, paid_currency=currency,
        track_id=track_id, status=status)
    if http is None:
        return 'transient'                      # network/timeout → retry
    if http == 200:
        slot = (body or {}).get("slot_id")
        logger.info(f"ALLOC_DONE | order={order_id} slot={slot} "
                    f"already={(body or {}).get('already_allocated', False)}")
        return 'terminal'
    if http in (502, 503):
        return 'transient'                      # verify_unavailable / backend busy → retry
    # 402 not_paid, 409 no_capacity/bad_state, 500 token_lost → terminal.
    code = (body or {}).get("code")
    if code in ("no_capacity", "amount_mismatch", "token_lost"):
        admin_alert(code, f"⚠️ Slot allocation failed for a PAID order "
                          f"<code>{order_id}</code>: {code}. Manual review/refund needed.")
    logger.warning(f"ALLOC_REFUSED | order={order_id} http={http} code={code}")
    return 'terminal'


def _process_allocate(order_id: str, track_id: str) -> str:
    """Verify the invoice with OxaPay then call the backend allocate. Returns
    'terminal' | 'transient' for the retry loop."""
    if not track_id:
        # No track id → let the backend re-verify by its own rules (it has the
        # stored track_id + its own OxaPay key).
        return _call_allocate(order_id)
    info = oxapay.get_payment(track_id)
    if not info.get("ok"):
        return 'transient'          # couldn't reach OxaPay → retry
    status = (info.get("status") or "").lower()
    if status in oxapay.PAID_STATUSES:
        return _call_allocate(order_id, amount=info.get("amount"),
                              currency=info.get("currency"), track_id=track_id, status=status)
    if status in oxapay.EXPIRED_STATUSES or status in oxapay.FAILED_STATUSES:
        return 'terminal'           # invoice dead → nothing to allocate
    logger.info(f"ALLOC_NOT_PAID_YET | order={order_id} status={status}")
    return 'terminal'


def _worker() -> None:
    logger.info("SLOT_ALLOC_WORKER_START")
    while True:
        try:
            try:
                order_id, track_id = _alloc_queue.get(timeout=60)
            except queue.Empty:
                continue
            try:
                for attempt in range(3):
                    outcome = _process_allocate(order_id, track_id)
                    if outcome == 'terminal':
                        break
                    time.sleep(2.0 * (attempt + 1))
            finally:
                _alloc_queue.task_done()
        except Exception:
            logger.exception("slot alloc worker error")
            time.sleep(1.0)


_alert_lock = threading.Lock()
_last_alert: "dict[str, float]" = {}
_ALERT_COOLDOWN = 300.0


def admin_alert(category: str, message: str) -> None:
    raw = (os.environ.get("ADMIN_TELEGRAM_ID") or "").strip()
    if not raw:
        return
    try:
        admin_id = int(raw)
    except (TypeError, ValueError):
        return
    now = time.time()
    with _alert_lock:
        if now - _last_alert.get(category, 0) < _ALERT_COOLDOWN:
            return
        _last_alert[category] = now
    try:
        from bot.telegram_api import send_message
        send_message(admin_id, message, parse_mode="HTML")
    except Exception:
        pass


def start_workers() -> None:
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_worker, daemon=True, name="slot-alloc-worker").start()
    logger.info("slot payment worker started")
