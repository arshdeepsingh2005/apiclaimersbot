"""
Gunicorn config for The Claimers Bot (Service 2).

This file intentionally sets NO server settings. bind, workers, timeout, worker
class, threads and logging all remain exactly as passed on the command line
(`--bind 0.0.0.0:$PORT --workers 1 --timeout 60`) — nothing here overrides them,
so production gunicorn behaviour is unchanged. The ONLY purpose of this file is
the post_worker_init hook below.

Why the hook: the OxaPay payment workers (credit-queue consumer + reconciliation)
must run INSIDE the gunicorn worker process, started AFTER the fork, so the
credit consumer shares the same in-memory queue object as the /pay/oxapay/webhook
producer. They were previously started at import time in bot_wsgi.py, which ran
them in the gunicorn MASTER process; the forked worker inherited a separate copy
of the queue, so webhook-enqueued payments were never dequeued and every payment
fell to the 7-minute reconciliation sweep. Threads do not survive fork(), so
post_worker_init is the correct place — this mirrors the main backend's
gunicorn.conf.py (theclaimers-main).
"""
import logging
import os

logger = logging.getLogger(__name__)


def post_worker_init(worker):
    """Called by gunicorn in each worker process immediately after it is forked
    and initialised. Starts the payment workers here so the producer (webhook)
    and consumer (credit worker) live in the same process and share one queue
    object. start_workers() is idempotent (per-process _started latch), and the
    deployment runs a single worker, so this starts exactly one credit worker +
    one reconciliation loop."""
    try:
        from bot import payments
        payments.start_workers()
        logger.info(
            f"post_worker_init: payment workers started in worker pid={os.getpid()}"
        )
    except Exception as exc:
        logger.error(
            f"post_worker_init: failed to start payment workers: {exc}",
            exc_info=True,
        )

    # Slot-sales allocation worker (OxaPay-paid order → allocate slot on backend).
    try:
        from bot import slot_payments
        slot_payments.start_workers()
        logger.info(
            f"post_worker_init: slot payment worker started in pid={os.getpid()}"
        )
    except Exception as exc:
        logger.error(
            f"post_worker_init: failed to start slot payment worker: {exc}",
            exc_info=True,
        )

    # Notification sender pool — same rationale as the payment workers: the
    # /nx/v3/push producer and these consumer threads must share one process +
    # queue object. Idempotent (per-process _started latch); single worker →
    # started exactly once. Keeps Telegram sends off the request threads.
    try:
        from bot import notify_queue
        notify_queue.start_workers()
        logger.info(
            f"post_worker_init: notify workers started in worker pid={os.getpid()}"
        )
    except Exception as exc:
        logger.error(
            f"post_worker_init: failed to start notify workers: {exc}",
            exc_info=True,
        )
