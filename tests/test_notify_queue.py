"""Unit + concurrency tests for bot.notify_queue (the non-blocking sender pool).

These mock `send_message` so nothing hits Telegram. They verify: enqueue routing
+ return values + counters, deterministic per-recipient sharding, full-queue
drop, send-time token resolution, 429/retry-after + transient-retry handling,
worker FIFO delivery, and worker resilience to a bad item.
"""
import queue
import threading
import time

import pytest

nq = pytest.importorskip("bot.notify_queue")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    # Drain all shard queues + reset counters before each test.
    for q in nq._queues:
        while True:
            try:
                q.get_nowait()
                q.task_done()
            except queue.Empty:
                break
    with nq._stats_lock:
        for k in nq._stats:
            nq._stats[k] = 0
    # By default: token_for returns "" (main), sleep is a no-op (fast tests).
    monkeypatch.setattr(nq.admin_routing, "token_for", lambda tid: "")
    monkeypatch.setattr(nq.time, "sleep", lambda *_a, **_k: None)
    yield


def _mock_send(monkeypatch, results):
    """Patch send_message to return successive `results` (list) or a constant."""
    calls = []
    seq = list(results) if isinstance(results, list) else None

    def fake(tid, msg, parse_mode="HTML"):
        calls.append((tid, msg))
        if seq is not None:
            return seq.pop(0) if seq else {"ok": True}
        return results

    monkeypatch.setattr(nq, "send_message", fake)
    return calls


# ------------------------------------------------------------------ enqueue
def test_enqueue_returns_queued_and_counts():
    assert nq.enqueue(1001, "hi") == "queued"
    assert nq.stats()["enqueued"] == 1
    assert nq.stats()["queue_depth"] == 1


def test_deterministic_sharding_same_user_same_shard():
    tid = 123456789
    idx = tid % nq.NOTIFY_WORKERS
    nq.enqueue(tid, "a")
    nq.enqueue(tid, "b")
    # both landed in the same shard, in order
    assert nq._queues[idx].qsize() == 2
    assert nq._queues[idx].get_nowait() == (tid, "a")
    assert nq._queues[idx].get_nowait() == (tid, "b")


def test_enqueue_invalid():
    assert nq.enqueue("not-an-int", "hi") == "invalid"
    assert nq.enqueue(1001, "") == "invalid"
    assert nq.stats()["enqueued"] == 0


def test_full_shard_drops_and_counts(monkeypatch):
    # Shrink a single shard to capacity 2 by filling it directly.
    tid = 4  # maps to a known shard
    idx = tid % nq.NOTIFY_WORKERS
    q = nq._queues[idx]
    # Fill to maxsize
    filled = 0
    while True:
        try:
            q.put_nowait((tid, "x"))
            filled += 1
        except queue.Full:
            break
    depth_before = q.qsize()
    # Next enqueue for this shard must drop
    assert nq.enqueue(tid, "overflow") == "full"
    assert nq.stats()["dropped_full"] == 1
    assert q.qsize() == depth_before  # nothing added


# --------------------------------------------------------------- _send_one
def test_send_one_success(monkeypatch):
    calls = _mock_send(monkeypatch, {"ok": True})
    nq._send_one(1001, "hi")
    assert nq.stats()["sent"] == 1 and nq.stats()["failed"] == 0
    assert calls == [(1001, "hi")]


def test_send_one_resolves_token_at_send_time(monkeypatch):
    seen = {}
    monkeypatch.setattr(nq.admin_routing, "token_for", lambda tid: seen.setdefault("tid", tid) or "TOK2")
    _mock_send(monkeypatch, {"ok": True})
    nq._send_one(777, "hi")
    assert seen["tid"] == 777  # token resolved for THIS recipient, at send time


def test_send_one_429_then_success(monkeypatch):
    slept = []
    monkeypatch.setattr(nq.time, "sleep", lambda s: slept.append(s))
    _mock_send(monkeypatch, [{"ok": False, "error_code": 429, "parameters": {"retry_after": 7}},
                             {"ok": True}])
    nq._send_one(1001, "hi")
    assert slept == [7]                      # honored retry_after
    assert nq.stats()["sent"] == 1


def test_send_one_429_capped(monkeypatch):
    slept = []
    monkeypatch.setattr(nq.time, "sleep", lambda s: slept.append(s))
    _mock_send(monkeypatch, [{"ok": False, "error_code": 429, "parameters": {"retry_after": 9999}},
                             {"ok": True}])
    nq._send_one(1001, "hi")
    assert slept == [nq._RETRY_CAP]          # capped, never unbounded


def test_send_one_429_twice_gives_up(monkeypatch):
    _mock_send(monkeypatch, [{"ok": False, "error_code": 429, "parameters": {"retry_after": 1}},
                             {"ok": False, "error_code": 429, "parameters": {"retry_after": 1}}])
    nq._send_one(1001, "hi")
    assert nq.stats()["failed"] == 1 and nq.stats()["sent"] == 0


def test_send_one_timeout_then_success(monkeypatch):
    _mock_send(monkeypatch, [{}, {"ok": True}])   # {} == timeout/connection
    nq._send_one(1001, "hi")
    assert nq.stats()["sent"] == 1


def test_send_one_timeout_twice_gives_up(monkeypatch):
    _mock_send(monkeypatch, [{}, {}])
    nq._send_one(1001, "hi")
    assert nq.stats()["failed"] == 1


def test_send_one_auth_error_no_retry(monkeypatch):
    calls = _mock_send(monkeypatch, {"ok": False, "error_code": 401, "description": "Unauthorized"})
    nq._send_one(1001, "hi")
    assert nq.stats()["failed"] == 1
    assert len(calls) == 1                    # 401 is not retried


def test_send_one_bad_request_no_retry(monkeypatch):
    calls = _mock_send(monkeypatch, {"ok": False, "error_code": 400, "description": "bad"})
    nq._send_one(1001, "hi")
    assert nq.stats()["failed"] == 1
    assert len(calls) == 1                    # 400 won't be fixed by retry


def test_send_one_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("telegram down")
    monkeypatch.setattr(nq, "send_message", boom)
    nq._send_one(1001, "hi")                   # must not raise
    assert nq.stats()["failed"] == 1


# ------------------------------------------------------------------ worker
def test_worker_delivers_in_fifo_order(monkeypatch):
    calls = []
    done = threading.Event()
    N = 5

    def fake(tid, msg, parse_mode="HTML"):
        calls.append(msg)
        if len(calls) == N:
            done.set()
        return {"ok": True}
    monkeypatch.setattr(nq, "send_message", fake)

    idx = 0
    t = threading.Thread(target=nq._worker, args=(idx,), daemon=True)
    t.start()
    # enqueue N messages that all map to shard 0 (tid % N == 0)
    tid = 0
    for i in range(N):
        nq._queues[idx].put((tid, f"m{i}"))
    assert done.wait(5), "worker did not drain in time"
    assert calls == [f"m{i}" for i in range(N)]   # strict FIFO within the shard
    assert nq.stats()["sent"] == N


def test_worker_survives_bad_item(monkeypatch):
    # A non-tuple item would blow up unpacking; the worker must recover + continue.
    calls = []
    done = threading.Event()

    def fake(tid, msg, parse_mode="HTML"):
        calls.append(msg)
        done.set()
        return {"ok": True}
    monkeypatch.setattr(nq, "send_message", fake)

    idx = 1
    t = threading.Thread(target=nq._worker, args=(idx,), daemon=True)
    t.start()
    nq._queues[idx].put("not-a-tuple")            # bad item → recovered
    nq._queues[idx].put((1, "good"))              # good item → still delivered
    assert done.wait(5)
    assert calls == ["good"]
