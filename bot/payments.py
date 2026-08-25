"""
Bot-side payment orchestration (Service 2).

The bot owns all OxaPay network I/O so the main backend carries no
payment-provider load. Main remains the system of record and the only thing
that moves money (it re-verifies before crediting). This module wires the three
flows together:

  1. start_topup()      — user clicks Top-Up: begin (reuse|allocate) on main,
                          create the OxaPay invoice here, record it back on main.
  2. webhook worker     — the OxaPay webhook handler verifies the HMAC, enqueues
                          the order, and returns 200 instantly. A background
                          worker thread drains the queue and asks main to verify
                          & credit. Keeping the network/credit work OFF the
                          request handler is what stops a slow provider from
                          tying up the bot's single sync web worker.
  3. reconciliation     — a periodic sweep asks main for open invoices and
                          re-checks each, finalising any payment whose webhook
                          was missed (bot/main briefly offline).

Idempotency: every path ultimately calls main's /topup/credit, which is keyed
on order_id and protected by a FOR UPDATE row lock + a `credited` latch, so
duplicate webhooks, concurrent reconciliation, and retried timeouts all credit
exactly once.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time
from uuid import uuid4

from bot import backend_client, oxapay

logger = logging.getLogger(__name__)

# Per-PROCESS identity for money-path diagnostics. Logged alongside pid and
# qid=id(_credit_queue) on ENQUEUE_OK / DEQUEUE / CREDIT_WORKER_START so we can
# prove the webhook producer and the credit consumer share the SAME process AND
# the SAME queue object — pid alone can't (a fork or module re-import can yield
# same-pid-but-different-object). Regenerated on every process start/restart.
_BOOT_ID = uuid4().hex[:8]

# Bounded so a flood of (HMAC-valid) webhooks can never exhaust memory; if it
# ever fills, reconciliation still finalises anything we drop.
_CREDIT_QUEUE_MAX = int(os.environ.get("CREDIT_QUEUE_MAX", "2000"))
_credit_queue: "queue.Queue[str]" = queue.Queue(maxsize=_CREDIT_QUEUE_MAX)

# Coalesce only near-simultaneous DUPLICATE deliveries of the same webhook.
# Kept short on purpose: OxaPay sends distinct status webhooks for one order
# ("paying" then "paid", often only seconds apart) and we must let the later
# "paid" one through to trigger the credit — a long window would swallow it and
# defer crediting to the reconciliation sweep. main is fully idempotent
# (order_id + FOR UPDATE + credited latch), so re-enqueuing is always safe.
_seen_lock = threading.Lock()
_recent_orders: "dict[str, float]" = {}
_RECENT_TTL = 3.0

# Inline retries are deliberately SHORT: they smooth over a momentary blip, but
# a longer outage must NOT stall the single worker thread (it would back the
# queue up and stall newer payments). Anything not finalised within these few
# seconds is left to the reconciliation sweep, which credits it idempotently on
# its next pass — so nothing is ever lost, the queue just drains fast.
_WEBHOOK_RETRIES = 2
_RETRY_BACKOFF = 2.0   # seconds, linear (≤ ~6s total per order)

# Results that mean "stop touching this order" (terminal or not-yet-but-later).
_TERMINAL_RESULTS = frozenset({
    "credited", "already_credited", "expired", "cancelled",
    "amount_mismatch", "no_license", "no_track", "not_recordable",
})


# ---------------------------------------------------------------------------
# 1) User-initiated top-up
# ---------------------------------------------------------------------------
def start_topup(telegram_id: int, amount: float) -> dict:
    """Returns a bot-friendly dict:
      {ok:True, reuse:bool, pay_url, order_id, amount}
      {ok:False, error:"<key>"}     (error keys map to friendly text in handlers)
    """
    # Fail fast (before allocating an order on main) if the bot can't create
    # invoices — avoids leaving an orphan 'new' row behind.
    if not oxapay.is_configured():
        logger.error("start_topup: OxaPay not configured on the bot")
        return {"ok": False, "error": "payments_unavailable"}

    begin = backend_client.begin_topup(telegram_id, amount)
    if begin is None:
        return {"ok": False, "error": "backend_unreachable"}
    if not begin.get("ok"):
        return {"ok": False, "error": begin.get("error", "unknown")}

    # Reuse path — main already has an open invoice; just hand back its link.
    if begin.get("reuse"):
        return {
            "ok": True,
            "reuse": True,
            "pay_url": begin.get("pay_url"),
            "order_id": begin.get("order_id"),
            "amount": begin.get("amount"),
        }

    # Create path — make the invoice with OxaPay (network I/O on the bot).
    order_id = begin.get("order_id")
    amt = begin.get("amount")
    if not oxapay.is_configured():
        logger.error("start_topup: OxaPay not configured on the bot")
        return {"ok": False, "error": "payments_unavailable"}

    inv = oxapay.create_invoice(amt, order_id, description=f"Top-up tg={telegram_id}")
    if not inv.get("ok"):
        logger.error(f"start_topup: invoice creation failed order={order_id}: {inv.get('error')}")
        return {"ok": False, "error": "invoice_failed"}

    rec = backend_client.record_topup(
        telegram_id,
        order_id=order_id,
        track_id=inv["track_id"],
        pay_url=inv["pay_url"],
        pay_address=inv.get("pay_address") or "",
        expires_at=inv.get("expired_at"),
    )
    if rec is None or not rec.get("ok"):
        # The invoice exists at OxaPay; reconciliation can still pick it up once
        # recorded, but recording failed, so surface a soft error to the user.
        logger.error(f"start_topup: record failed order={order_id}: {rec}")
        return {"ok": False, "error": "invoice_failed"}

    return {
        "ok": True,
        "reuse": False,
        "pay_url": inv["pay_url"],
        "order_id": order_id,
        "amount": amt,
    }


# ---------------------------------------------------------------------------
# 2) Webhook → credit queue
# ---------------------------------------------------------------------------
def enqueue_credit(order_id: str, track_id: str = "") -> str:
    """Called by the (already HMAC-verified) webhook handler. Non-blocking;
    de-dupes recent orders and never raises. Carries the track_id so the credit
    worker can verify with OxaPay (on the reliable sync worker) before forwarding.

    Returns a status string: "enqueued" | "coalesced" | "full" | "invalid".
    The authoritative "it is really on the queue" signal (ENQUEUE_OK) is logged
    HERE, only on a successful real put — never by the caller (whose old
    "queued" log fired even when the put was coalesced or dropped)."""
    order_id = (order_id or "").strip()
    if not order_id:
        return "invalid"
    track_id = (track_id or "").strip()
    now = time.time()
    with _seen_lock:
        # prune
        if len(_recent_orders) > 4096:
            for k in [k for k, t in _recent_orders.items() if now - t > _RECENT_TTL]:
                _recent_orders.pop(k, None)
        last = _recent_orders.get(order_id)
        if last is not None and now - last < _RECENT_TTL:
            logger.info(f"ENQUEUE_COALESCED | order={order_id}")
            return "coalesced"  # already queued/handled very recently — coalesce
        _recent_orders[order_id] = now
    try:
        _credit_queue.put_nowait((order_id, track_id))
        logger.info(
            f"ENQUEUE_OK | order={order_id} | track={track_id or '?'} | "
            f"qsize={_credit_queue.qsize()} | boot={_BOOT_ID} | pid={os.getpid()} | "
            f"qid={id(_credit_queue)} | thread={threading.current_thread().name}"
        )
        return "enqueued"
    except queue.Full:
        logger.warning(
            f"ENQUEUE_FULL | order={order_id} | qsize={_credit_queue.qsize()} "
            "(reconciliation will recover)"
        )
        return "full"


def _forward_credit(order_id: str, verified: bool) -> str:
    """POST main /topup/credit and classify: 'terminal' | 'transient'.
    `verified` = the bot has confirmed PAID via its own OxaPay inquiry."""
    http, body = backend_client.credit_topup(order_id, verified=verified)
    if http is None:
        return "transient"            # transport failure to MAIN
    if http >= 500:
        return "transient"            # main flagged verify/credit retryable
    if http == 200 and isinstance(body, dict):
        result = body.get("result")
        if result in _TERMINAL_RESULTS:
            logger.info(f"CREDIT_DONE | order={order_id} | result={result} | verified={verified}")
            return "terminal"
        if result == "not_paid_yet":
            logger.info(f"CREDIT_PENDING | order={order_id}")
            return "terminal"
    logger.warning(f"CREDIT_UNRETRYABLE | order={order_id} | http={http} | body={body}")
    return "terminal"


def _process_credit(order_id: str, track_id: str) -> str:
    """Verify the invoice with OxaPay HERE (the bot's sync worker reaches OxaPay
    reliably) and forward to main only when appropriate. Returns 'terminal' |
    'transient' for the retry loop.

      • bot sees PAID              → forward verified=True (main credits; it can
                                     trust us if its own OxaPay call is failing)
      • bot sees expired/failed    → forward verified=True to retire the invoice
      • bot sees waiting/confirming→ not paid yet; stop (reconcile re-checks)
      • bot CAN'T reach OxaPay     → forward verified=False (let main try/​retry)
      • no track_id                → forward verified=False (main verifies)
    """
    track_id = (track_id or "").strip()
    if not track_id:
        return _forward_credit(order_id, verified=False)
    info = oxapay.get_payment(track_id)
    if not info.get("ok"):
        # The bot couldn't verify either — let main try (and retry).
        return _forward_credit(order_id, verified=False)
    status = info.get("status") or ""
    if status in oxapay.PAID_STATUSES:
        # verified=True means EXCLUSIVELY "the bot confirmed this invoice is
        # PAID" — the only thing main may credit on trust. NEVER send it for a
        # non-paid status.
        return _forward_credit(order_id, verified=True)
    if status in oxapay.EXPIRED_STATUSES or status in oxapay.FAILED_STATUSES:
        # Retire terminal invoices with verified=False: main will mark them
        # expired/cancelled only if IT can verify with OxaPay. If main can't
        # verify, it must NOT act on our word (a false 'expired' is harmless,
        # but crediting a cancelled invoice would not be) — so no trust here.
        return _forward_credit(order_id, verified=False)
    # waiting / confirming → genuinely not paid yet; don't hot-loop.
    logger.info(f"CREDIT_NOT_PAID_YET | order={order_id} | status={status}")
    return "terminal"


def _credit_worker() -> None:
    logger.info(
        f"CREDIT_WORKER_START | boot={_BOOT_ID} | pid={os.getpid()} | "
        f"qid={id(_credit_queue)} | thread={threading.current_thread().name}"
    )
    processed = 0
    while True:
        # Outer guard: recover from any *normal* runtime error (Exception only —
        # never BaseException, so SystemExit/KeyboardInterrupt/GeneratorExit still
        # stop the thread on real shutdown). The loop keeps consuming; it does not
        # die silently the way the old bare-get() version could.
        try:
            try:
                item = _credit_queue.get(timeout=60)
            except queue.Empty:
                # Idle heartbeat — only reachable because get() has a timeout.
                # A stuck qsize here would prove "alive but not draining THIS object".
                logger.info(
                    f"CREDIT_WORKER_HEARTBEAT | boot={_BOOT_ID} | pid={os.getpid()} | "
                    f"qid={id(_credit_queue)} | processed={processed} | "
                    f"qsize={_credit_queue.qsize()}"
                )
                continue  # no item → do NOT call task_done()
            # We hold a real item: task_done() must run exactly once, so it lives
            # in this inner finally (the queue.Empty path continued above).
            try:
                order_id, track_id = item if isinstance(item, tuple) else (item, "")
                logger.info(
                    f"DEQUEUE | order={order_id} | qsize={_credit_queue.qsize()} | "
                    f"boot={_BOOT_ID} | pid={os.getpid()} | qid={id(_credit_queue)} | "
                    f"thread={threading.current_thread().name}"
                )
                for attempt in range(_WEBHOOK_RETRIES):
                    outcome = _process_credit(order_id, track_id)
                    if outcome == "terminal":
                        break
                    time.sleep(_RETRY_BACKOFF * (attempt + 1))
                processed += 1
            finally:
                _credit_queue.task_done()
        except Exception:
            logger.error("CREDIT_WORKER_LOOP_ERROR (recovered)", exc_info=True)
            time.sleep(0.5)


# ---------------------------------------------------------------------------
# 3) Reconciliation sweep (missed-webhook recovery)
# ---------------------------------------------------------------------------
def reconcile_once() -> int:
    """Re-check all open invoices once. To keep the main backend's OxaPay load
    minimal, the bot PRE-FILTERS here with its own inquiry and only forwards
    invoices that OxaPay reports as paid to main's credit endpoint (main then
    re-verifies authoritatively before moving money). Returns how many paid
    invoices were forwarded."""
    pending = backend_client.get_pending(
        limit=int(os.environ.get("RECONCILE_LIMIT", "200")),
        lookback_hours=int(os.environ.get("RECONCILE_LOOKBACK_HOURS", "72")),
    )
    checked = 0
    for p in pending:
        oid = (p.get("order_id") or "").strip()
        track = (p.get("track_id") or "").strip()
        if not oid or not track:
            continue
        try:
            # Same path as the webhook worker: the bot verifies with OxaPay and
            # forwards PAID/terminal to main (verified=True), so main credits even
            # if its own OxaPay call is failing. Still-open invoices are skipped.
            _process_credit(oid, track)   # synchronous; sweep runs in its own thread
            checked += 1
        except Exception as exc:
            logger.warning(f"reconcile order={oid} failed: {exc}")
    if checked:
        logger.info(f"RECONCILE | checked {checked} open invoice(s)")
    return checked


def _reconcile_loop() -> None:
    try:
        interval_min = float(os.environ.get("RECONCILE_INTERVAL_MIN", "7"))
    except (TypeError, ValueError):
        interval_min = 7.0
    interval = max(60.0, interval_min * 60.0)
    # Small initial delay so startup (token check, cache load) settles first.
    time.sleep(30)
    while True:
        try:
            reconcile_once()
        except Exception as exc:
            logger.error(f"reconcile loop error: {exc}", exc_info=True)
        time.sleep(interval)


# ---------------------------------------------------------------------------
# Debounced admin alerts (security/ops events; one per category per cooldown)
# ---------------------------------------------------------------------------
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


_started = False
_start_lock = threading.Lock()


def start_workers() -> None:
    """Idempotently start the credit worker + reconciliation threads. Called
    once from the bot startup routine."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_credit_worker, daemon=True, name="oxapay-credit-worker").start()
    threading.Thread(target=_reconcile_loop, daemon=True, name="oxapay-reconcile").start()
    logger.info(
        f"PAYMENT_WORKERS_START | boot={_BOOT_ID} | pid={os.getpid()} | "
        f"qid={id(_credit_queue)} | credit_worker+reconcile started"
    )
