"""Load / isolation tests for the notification queue — the 'hard gate'.

These model the actual bottleneck (the bounded gunicorn thread pool) and prove
the core claim: an enqueue-based notify_push keeps request-acceptance latency
FLAT under a notification flood, whereas the old synchronous send starves it.
Also: shard independence, and worker resilience under a total Telegram outage.

No real Telegram is ever contacted (send_message is stubbed for the whole file).
"""
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

nq = pytest.importorskip("bot.notify_queue")


@pytest.fixture(autouse=True)
def _safe(monkeypatch):
    # Never hit real Telegram, even if a worker thread runs; reset state.
    monkeypatch.setattr(nq, "send_message", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(nq.admin_routing, "token_for", lambda tid: "")
    for q in nq._queues:
        try:
            while True:
                q.get_nowait()
                q.task_done()
        except queue.Empty:
            pass
    with nq._stats_lock:
        for k in nq._stats:
            nq._stats[k] = 0
    yield


def test_flat_request_latency_under_flood():
    """Model the 8-gthread pool. A 400-message flood must NOT delay an
    'admin webhook' task when the handler only enqueues; a synchronous handler
    (the OLD behavior) demonstrably does delay it — proving the test detects the
    starvation it claims to fix."""
    POOL_SIZE, N, SEND = 8, 400, 0.02

    def enqueue_handler(i):        # NEW notify_push work: just enqueue (µs)
        nq.enqueue(i, "m")

    def sync_handler(i):           # OLD notify_push work: synchronous Telegram send
        time.sleep(SEND)

    def admin_latency(handler):
        pool = ThreadPoolExecutor(max_workers=POOL_SIZE)
        try:
            for i in range(N):     # saturate the pool with the flood
                pool.submit(handler, i)
            t0 = time.perf_counter()               # an admin webhook arrives now
            fut = pool.submit(time.perf_counter)   # ...waits for a free thread
            return fut.result() - t0
        finally:
            pool.shutdown(wait=True)

    new_lat = admin_latency(enqueue_handler)
    old_lat = admin_latency(sync_handler)
    print(f"\n  admin-accept latency under {N}-flood:  new(enqueue)={new_lat*1000:.1f}ms  "
          f"old(sync)={old_lat*1000:.1f}ms")

    assert new_lat < 0.15, f"enqueue handler still starved: {new_lat*1000:.0f}ms"
    assert old_lat > 0.5, (f"test not detecting starvation "
                           f"(old={old_lat*1000:.0f}ms) — cannot trust the flat result")
    assert old_lat > new_lat * 5


def test_one_full_shard_does_not_block_other_shards():
    """Worst-case sharding: fill one shard completely; other shards must still
    accept and only the full shard drops."""
    assert nq.NOTIFY_WORKERS >= 2
    idx0 = 0
    q0 = nq._queues[idx0]
    while True:                              # fill shard 0 to capacity
        try:
            q0.put_nowait((0, "x"))
        except queue.Full:
            break
    assert nq.enqueue(0, "overflow") == "full"        # shard 0 rejects
    assert nq.stats()["dropped_full"] == 1
    # a recipient that maps to a DIFFERENT shard is unaffected
    other = 1                                          # 1 % N == 1 (N >= 2)
    assert other % nq.NOTIFY_WORKERS != idx0
    assert nq.enqueue(other, "ok") == "queued"
    assert nq._queues[other % nq.NOTIFY_WORKERS].qsize() >= 1


def test_worker_survives_total_telegram_outage(monkeypatch):
    """Telegram completely unavailable: the worker must retry→fail→continue
    (never die), keep draining, and the request path (enqueue) must stay instant
    with the queue bounded."""
    monkeypatch.setattr(nq.time, "sleep", lambda *_a, **_k: None)   # skip retry waits
    monkeypatch.setattr(nq, "send_message", lambda *a, **k: {})     # every send 'times out'

    processed = threading.Semaphore(0)
    real_send_one = nq._send_one

    def counting_send_one(tid, message):
        real_send_one(tid, message)
        processed.release()
    monkeypatch.setattr(nq, "_send_one", counting_send_one)

    idx = 0
    threading.Thread(target=nq._worker, args=(idx,), daemon=True).start()

    M = 60
    for i in range(M):
        nq._queues[idx].put((0, f"m{i}"))

    for _ in range(M):                       # every item is drained despite outage
        assert processed.acquire(timeout=5), "worker stalled/died under outage"
    assert nq.stats()["failed"] == M
    assert nq.stats()["sent"] == 0

    # request path stays instant during the outage
    t0 = time.perf_counter()
    nq.enqueue(1, "still-fast")
    assert time.perf_counter() - t0 < 0.01
