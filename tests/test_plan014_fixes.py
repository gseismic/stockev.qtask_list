"""PLAN-014 修复项测试：去重、退避、deadline 跳过、外存错误分类、DLQ 重放重置、
push_batch 新参数、归档仅 terminal、信封保留 expires_at。"""
import json
import os
import shutil
import time

import pytest
import redis

from qtask_list import SmartQueue, Worker
from qtask_list.archiver import ArchiveManager


@pytest.fixture
def redis_url():
    return "redis://localhost:6379/0"


@pytest.fixture
def r(redis_url):
    client = redis.from_url(redis_url, decode_responses=True)
    yield client
    for k in client.scan_iter("testns:*"):
        client.delete(k)
    for k in client.scan_iter("qtask:task:*"):
        client.delete(k)
    for k in client.scan_iter("qtask:hist:testns:*"):
        client.delete(k)


class BrokenLoadStorage:
    """save 正常、load 抛网络错误的假外存，用于测试瞬时故障路径。"""

    def save_bytes(self, data):
        return "broken-key-1"

    def load(self, key):
        raise ConnectionError("storage service down")


class TestLogicalKeyDedup:
    def test_push_dedup_same_key(self, redis_url, r):
        q = SmartQueue(redis_url, "dedup", namespace="testns")
        t1 = q.push({"action": "fetch"}, logical_key="kline:AAPL:1d:2026-09-19")
        t2 = q.push({"action": "fetch"}, logical_key="kline:AAPL:1d:2026-09-19")

        assert t1 is not None
        assert t2 is None
        assert r.llen("testns:dedup") == 1
        # 去重键带 TTL
        assert r.ttl(f"{q.dedup_prefix}kline:AAPL:1d:2026-09-19") > 0
        # 不同键不受影响
        t3 = q.push({"action": "fetch"}, logical_key="kline:TSLA:1d:2026-09-19")
        assert t3 is not None
        assert r.llen("testns:dedup") == 2

    def test_push_force_bypasses_dedup(self, redis_url, r):
        q = SmartQueue(redis_url, "dedup_force", namespace="testns")
        q.push({"action": "fetch"}, logical_key="news:abc")
        t2 = q.push({"action": "fetch"}, logical_key="news:abc", force=True)

        assert t2 is not None
        assert r.llen("testns:dedup_force") == 2

    def test_push_dedup_ttl_default_follows_expire(self, redis_url, r):
        q = SmartQueue(redis_url, "dedup_ttl", namespace="testns")
        q.push({"action": "fetch"}, expire_seconds=300, logical_key="quote:AAPL:1310")
        ttl = r.ttl("testns:dedup_ttl:dedup:quote:AAPL:1310")
        assert 500 <= ttl <= 600  # expire_seconds * 2

    def test_push_batch_logical_keys(self, redis_url, r):
        q = SmartQueue(redis_url, "dedup_batch", namespace="testns")
        ids = q.push_batch(
            [{"action": "n", "i": 1}, {"action": "n", "i": 2}],
            logical_keys=["news:1", "news:2"],
        )
        assert all(ids)

        # 第二轮发现：前两条被去重，第三条是新条目
        ids2 = q.push_batch(
            [{"action": "n", "i": 1}, {"action": "n", "i": 2}, {"action": "n", "i": 3}],
            logical_keys=["news:1", "news:2", "news:3"],
        )
        assert ids2 == [None, None, ids2[2]]
        assert ids2[2] is not None
        assert r.llen("testns:dedup_batch") == 3

    def test_push_batch_logical_keys_length_mismatch(self, redis_url, r):
        q = SmartQueue(redis_url, "dedup_mismatch", namespace="testns")
        with pytest.raises(ValueError):
            q.push_batch([{"action": "n"}], logical_keys=["a", "b"])


class TestRetryBackoff:
    def test_fail_goes_to_delay_with_backoff(self, redis_url, r):
        q = SmartQueue(redis_url, "backoff", namespace="testns", max_retry=3)  # 默认 base=30
        tid = q.push({"action": "b"})
        _payload, raw = q.pop(timeout=1)

        before = time.time()
        assert q.fail(raw, "err") is True

        assert r.zcard("testns:backoff:delay") == 1
        assert r.llen("testns:backoff:retry") == 0
        member = r.zrange("testns:backoff:delay", 0, -1)[0]
        score = r.zscore("testns:backoff:delay", member)
        assert before + 25 <= score <= before + 40  # 30s ±10% 抖动
        assert q.history.get(tid)["status"] == "retry"

    def test_backoff_exhaustion_reaches_dlq(self, redis_url, r):
        q = SmartQueue(redis_url, "backoff_dlq", namespace="testns", max_retry=2)
        q.push({"action": "b"})
        _p, raw = q.pop(timeout=1)
        q.fail(raw, "err")  # _retry=1 → delay

        # 推迟到期并搬回主队列，第二次失败耗尽 → DLQ
        member = r.zrange("testns:backoff_dlq:delay", 0, -1)[0]
        r.zadd("testns:backoff_dlq:delay", {member: time.time() - 1})
        assert q.move_delay() == 1

        payload, raw = q.pop(timeout=1)
        assert payload["_retry"] == 1
        q.fail(raw, "err")
        assert r.llen("testns:backoff_dlq:dlq") == 1

    def test_backoff_preserves_expires_at_envelope(self, redis_url, r):
        q = SmartQueue(redis_url, "backoff_env", namespace="testns", max_retry=3)
        q.push({"action": "b"}, expire_seconds=3600)
        _p, raw = q.pop(timeout=1)
        q.fail(raw, "err")

        member = r.zrange("testns:backoff_env:delay", 0, -1)[0]
        envelope = json.loads(member)
        assert "expires_at" in envelope


class TestDeadlineSkip:
    def test_expired_task_skipped_at_pop(self, redis_url, r):
        q = SmartQueue(redis_url, "expired", namespace="testns")
        tid = q.push({"action": "stale"}, expire_seconds=3600)
        # 把 ready 消息中的 expires_at 改成过去，模拟排队超时
        msg = r.lindex("testns:expired", 0)
        data = json.loads(msg)
        data["expires_at"] = time.time() - 1
        r.lset("testns:expired", 0, json.dumps(data))

        payload, raw = q.pop(timeout=1)

        assert payload is None and raw is None
        assert r.llen("testns:expired:processing") == 0
        assert r.llen("testns:expired:dlq") == 0  # 过期跳过不进 DLQ
        assert r.llen("testns:expired") == 0
        assert q.history.get(tid)["status"] == "skipped"

    def test_pop_continues_after_skipping_expired(self, redis_url, r):
        q = SmartQueue(redis_url, "expired_next", namespace="testns")
        expired_msg = json.dumps({
            "task_id": "expired-1",
            "payload": json.dumps({"action": "stale"}),
            "expires_at": time.time() - 1,
        })
        r.lpush("testns:expired_next", expired_msg)
        q.push({"action": "live"})

        payload, raw = q.pop(timeout=1)

        assert payload["action"] == "live"
        assert r.llen("testns:expired_next:processing") == 1
        q.ack(raw)


class TestLargePayloadErrors:
    def test_transient_storage_failure_defers_not_dlq(self, redis_url, r):
        storage = BrokenLoadStorage()
        q = SmartQueue(redis_url, "large_transient", namespace="testns",
                       storage=storage, large_threshold=10)
        q.push({"action": "big", "data": "x" * 100})

        payload, raw = q.pop(timeout=1)

        assert payload is None and raw is None
        assert r.zcard("testns:large_transient:delay") == 1  # 延后重试
        assert r.llen("testns:large_transient:dlq") == 0
        assert r.llen("testns:large_transient:processing") == 0

    def test_large_without_storage_goes_dlq(self, redis_url, r):
        q_push = SmartQueue(redis_url, "large_asym", namespace="testns",
                            storage=BrokenLoadStorage(), large_threshold=10)
        q_push.push({"action": "big", "data": "x" * 100})

        # 消费端未配置 storage：配置错误，进 DLQ 而不是延后
        q_pop = SmartQueue(redis_url, "large_asym", namespace="testns")
        payload, raw = q_pop.pop(timeout=1)

        assert payload is None and raw is None
        assert r.llen("testns:large_asym:dlq") == 1
        assert r.llen("testns:large_asym:processing") == 0


class TestDlqRequeueReset:
    def test_requeue_dlq_resets_retry(self, redis_url, r):
        q = SmartQueue(redis_url, "rq_reset", namespace="testns", max_retry=1)
        q.push({"action": "r"})
        _p, raw = q.pop(timeout=1)
        q.fail(raw, "e")  # _retry=1 >= 1 → DLQ

        assert q.requeue_dlq() == 1
        payload, raw = q.pop(timeout=1)
        assert payload.get("_retry", 0) == 0

    def test_requeue_dlq_keep_retry(self, redis_url, r):
        q = SmartQueue(redis_url, "rq_keep", namespace="testns", max_retry=1)
        q.push({"action": "r"})
        _p, raw = q.pop(timeout=1)
        q.fail(raw, "e")

        assert q.requeue_dlq(reset_retry=False) == 1
        payload, raw = q.pop(timeout=1)
        assert payload["_retry"] == 1


class TestPushBatchParams:
    def test_push_batch_delay(self, redis_url, r):
        q = SmartQueue(redis_url, "batch_delay", namespace="testns")
        q.push_batch([{"action": "a"}, {"action": "b"}], delay_seconds=60)
        assert r.zcard("testns:batch_delay:delay") == 2
        assert r.llen("testns:batch_delay") == 0

    def test_push_batch_expire_envelope(self, redis_url, r):
        q = SmartQueue(redis_url, "batch_expire", namespace="testns")
        q.push_batch([{"action": "a"}], expire_seconds=120)
        msg = r.lindex("testns:batch_expire", 0)
        assert "expires_at" in json.loads(msg)


class TestArchiveTerminalOnly:
    def test_live_task_history_not_archived(self, redis_url, r):
        q = SmartQueue(redis_url, "term_arch", namespace="testns")
        tid_live = q.push({"action": "live"})  # pending，仍在队列
        tid_done = q.push({"action": "done"})
        q.history.update(tid_done, {"status": "completed"})
        two_days_ago = time.time() - 2 * 86400
        r.zadd(q.history.idx_key, {tid_live: two_days_ago, tid_done: two_days_ago})

        archiver = ArchiveManager(redis_url, db_dir="./test_arch_term")
        count = archiver.archive_to_sqlite("testns:term_arch", days_ago=1)

        assert count == 1
        assert r.exists(f"qtask:task:{tid_live}") == 1  # live 历史保留
        assert r.zscore(q.history.idx_key, tid_live) is not None
        assert r.exists(f"qtask:task:{tid_done}") == 0  # terminal 已归档
        assert r.zscore(q.history.idx_key, tid_done) is None

        if os.path.exists("./test_arch_term"):
            shutil.rmtree("./test_arch_term")


class TestZstdThreadLocal:
    def test_compress_roundtrip_multithread(self, redis_url, r):
        q = SmartQueue(redis_url, "zstd_mt", namespace="testns", compress_threshold=50)
        big = {"action": "big", "rows": ["x" * 40] * 20}

        import threading
        errors = []

        def push_one():
            try:
                q.push(dict(big))
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=push_one) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

        for _ in range(8):
            payload, raw = q.pop(timeout=1)
            assert payload["action"] == "big"
            q.ack(raw)


class TestWorkerParams:
    def test_worker_passes_through_backoff_and_archive_dir(self, redis_url, r):
        worker = Worker(
            redis_url,
            "params",
            namespace="testns",
            retry_backoff_base=77,
            retry_backoff_max=500,
            record_history=False,
        )
        assert worker.queue.retry_backoff_base == 77
        assert worker.queue.retry_backoff_max == 500
        assert worker.queue.record_history is False
        assert os.path.isabs(worker.archive_dir)
        assert worker.stale_recover_interval >= 30

    def test_record_history_false_skips_history(self, redis_url, r):
        q = SmartQueue(redis_url, "nohist", namespace="testns", record_history=False)
        tid = q.push({"action": "x"})
        assert tid is not None
        assert r.exists(f"qtask:task:{tid}") == 0
        assert r.zcard("qtask:hist:testns:nohist") == 0

        # 消费闭环不受影响
        payload, raw = q.pop(timeout=1)
        assert payload["action"] == "x"
        assert q.ack(raw) is True
