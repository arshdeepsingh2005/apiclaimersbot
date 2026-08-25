"""
Non-blocking Telegram notification delivery for the bot.

WHY THIS EXISTS
---------------
`/nx/v3/push` (backend → bot notification relay) used to call `send_message()`
SYNCHRONOUSLY inside the gunicorn request thread. Under a claim-drop flood that
held all 8 gthreads at once, starving BOTH bots' webhooks — the admin's commands
(e.g. /valuefornextcode) couldn't even be *accepted* until a thread freed.

This module moves the actual Telegram send OFF the request thread. `notify_push`
now only validates + `enqueue()`s and returns 200 instantly; a small fixed pool
of daemon workers drains bounded, per-recipient-sharded queues and does the
sends. Mirrors bot/payments.py's queue + worker pattern.

GUARANTEES
----------
  * The request path (notify_push) does NO Telegram I/O → the 8 gthreads are
    never held by a send → webhooks (incl. admin Bot 2) are always accepted.
  * Per-recipient FIFO: a telegram_id always maps to the same worker
    (telegram_id % NOTIFY_WORKERS), so one user's messages stay ordered.
  * Bounded memory: fixed maxsize per shard; on full we drop + count (best-effort,
    exactly as today — the backend already fires notifications fire-and-forget).
  * Workers own ALL failure handling (429/retry_after, timeout, revoked token,
    bad item) so a Telegram throttle/outage can never reach the request threads.
  * No application lock is held during Telegram I/O; the only shared structures
    are the thread-safe per-shard queues + a tiny counters lock used nowhere else.
"""
from __future__ import annotations

import logging
import os
import queue
import threading
import time

from bot import admin_routing
from bot.telegram_api import send_message, use_token

logger = logging.getLogger(__name__)

# Fixed pool + bounded TOTAL capacity, split evenly across shards.
NOTIFY_WORKERS = max(1, int(os.environ.get("NOTIFY_WORKERS", "4")))
NOTIFY_QUEUE_MAX = int(os.environ.get("NOTIFY_QUEUE_MAX", "5000"))
_SHARD_MAX = max(1, NOTIFY_QUEUE_MAX // NOTIFY_WORKERS)   # e.g. 5000//4 = 1250/shard

# Honor a 429 retry_after up to this many seconds (never sleep unbounded).
_RETRY_CAP = int(os.environ.get("NOTIFY_RETRY_CAP", "30"))
# Periodic one-line summary cadence.
_SUMMARY_INTERVAL = float(os.environ.get("NOTIFY_SUMMARY_INTERVAL", "30"))

# One bounded queue per worker (shard). item = (telegram_id: int, message: str)
_queues: list[queue.Queue] = [queue.Queue(maxsize=_SHARD_MAX) for _ in range(NOTIFY_WORKERS)]

# Counters — guarded by a lock used ONLY here (never couples with app locks).
_stats_lock = threading.Lock()
_stats = {"enqueued": 0, "sent": 0, "failed": 0, "dropped_full": 0}


def _bump(key: str, n: int = 1) -> None:
    with _stats_lock:
        _stats[key] += n


def enqueue(telegram_id, message: str) -> str:
    """Queue a message for delivery. Returns 'queued' | 'full' | 'invalid'.

    NON-BLOCKING and never raises — safe to call from the gunicorn request thread.
    On a full shard the message is dropped and counted (best-effort delivery,
    same guarantee the backend already relies on); NO per-message log so a flood
    cannot recreate the logging-pressure problem this whole change removes.
    """
    try:
        tid = int(telegram_id)
    except (TypeError, ValueError):
        return "invalid"
    if not message:
        return "invalid"
    q = _queues[tid % NOTIFY_WORKERS]
    try:
        q.put_nowait((tid, message))
    except queue.Full:
        _bump("dropped_full")
        return "full"
    _bump("enqueued")
    return "queued"


def _send_one(tid: int, message: str) -> None:
    """Deliver one message. Resolves the CURRENT token at send time (Bot 2 for
    admin-allowlisted recipients, else main), with 429 + one-shot transient
    retry. Never raises; always accounts the outcome."""
    tok = admin_routing.token_for(tid)   # currently-valid routing, not frozen at enqueue
    for attempt in (1, 2):
        try:
            with use_token(tok):
                res = send_message(tid, message, parse_mode="HTML")
        except Exception as exc:  # send_message is best-effort, but guard anyway
            logger.error("notify: send raised for tid=%s: %s", tid, exc)
            _bump("failed")
            return

        if isinstance(res, dict) and res.get("ok"):
            _bump("sent")
            logger.debug("notify: delivered tid=%s", tid)   # DEBUG, never INFO
            return

        code = res.get("error_code") if isinstance(res, dict) else None
        if attempt == 1:
            # 429: Telegram is throttling this token — honor retry_after once.
            if code == 429:
                try:
                    retry_after = int(res.get("parameters", {}).get("retry_after", 1))
                except Exception:
                    retry_after = 1
                time.sleep(min(max(retry_after, 1), _RETRY_CAP))
                continue
            # {} == timeout/connection failure — one quick retry.
            if not res:
                time.sleep(0.5)
                continue
            # Any other non-ok (e.g. 400 bad request) won't be fixed by retrying.

        # Give up: loud on auth/token problems (surfaces config errors as today),
        # quiet+counted otherwise.
        if isinstance(res, dict) and code in (401, 403):
            logger.error("notify: auth/token error for tid=%s: %s", tid, res.get("description"))
        else:
            logger.debug("notify: drop tid=%s after %d attempt(s): %s",
                         tid, attempt, (res.get("description") if isinstance(res, dict) else res))
        _bump("failed")
        return


def _worker(idx: int) -> None:
    q = _queues[idx]
    logger.info("notify worker %d started (shard_max=%d)", idx, _SHARD_MAX)
    while True:
        # Outer guard: recover from any normal runtime error (Exception only, so
        # real shutdown signals still stop the thread) — the worker never dies
        # silently the way a bare get()/send() could.
        try:
            try:
                tid, message = q.get(timeout=60)
            except queue.Empty:
                continue  # idle → do NOT call task_done()
            try:
                _send_one(tid, message)
            finally:
                q.task_done()
        except Exception:
            logger.error("notify worker %d loop error (recovered)", idx, exc_info=True)
            time.sleep(0.5)


def _summary_loop() -> None:
    """One INFO line every _SUMMARY_INTERVAL, but only when something changed —
    no per-notification logging anywhere."""
    last = None
    while True:
        time.sleep(_SUMMARY_INTERVAL)
        with _stats_lock:
            snap = dict(_stats)
        depth = sum(q.qsize() for q in _queues)
        cur = (snap["enqueued"], snap["sent"], snap["failed"], snap["dropped_full"], depth)
        if cur != last:
            logger.info("notify: enq=%d sent=%d failed=%d dropped=%d depth=%d",
                        snap["enqueued"], snap["sent"], snap["failed"], snap["dropped_full"], depth)
            last = cur


_started = False
_start_lock = threading.Lock()


def start_workers() -> None:
    """Idempotently start the sender pool + summary thread. Called once per
    process from gunicorn.conf.py post_worker_init (threads don't survive fork)."""
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    for i in range(NOTIFY_WORKERS):
        threading.Thread(target=_worker, args=(i,), daemon=True,
                         name=f"notify-worker-{i}").start()
    threading.Thread(target=_summary_loop, daemon=True, name="notify-summary").start()
    logger.info("NOTIFY_WORKERS_START | workers=%d shard_max=%d total_max=%d | pid=%d",
                NOTIFY_WORKERS, _SHARD_MAX, NOTIFY_QUEUE_MAX, os.getpid())


def stats() -> dict:
    """Snapshot for tests/diagnostics."""
    with _stats_lock:
        snap = dict(_stats)
    snap["queue_depth"] = sum(q.qsize() for q in _queues)
    return snap
