import pytest
import redis
from qtask_list import SmartQueue


@pytest.fixture
def redis_url():
    return "redis://localhost:6379/0"


@pytest.fixture
def r(redis_url):
    client = redis.from_url(redis_url, decode_responses=True)
    yield client
    keys = client.keys("testns:*")
    for k in keys:
        client.delete(k)


class TestSmartQueue:
    def test_push_and_pop(self, redis_url, r):
        q = SmartQueue(redis_url, "test", namespace="testns")
        task_id = q.push({"action": "test", "data": "hello"})
        
        assert task_id is not None
        assert r.llen("testns:test") == 1
        
        payload, raw = q.pop()
        assert payload["action"] == "test"
        assert payload["data"] == "hello"
        
        q.ack(raw)
        assert r.llen("testns:test:processing") == 0

    def test_batch_push(self, redis_url, r):
        q = SmartQueue(redis_url, "batch_test", namespace="testns")
        task_ids = q.push_batch([
            {"action": "test", "i": 1},
            {"action": "test", "i": 2},
            {"action": "test", "i": 3},
        ])
        
        assert len(task_ids) == 3
        assert r.llen("testns:batch_test") == 3

    def test_fail_and_retry(self, redis_url, r):
        q = SmartQueue(redis_url, "retry_test", namespace="testns", max_retry=2)
        task_id = q.push({"action": "test"})
        
        payload, raw = q.pop()
        q.fail(raw, "test error")
        
        # 失败后进入 retry 队列
        assert r.llen("testns:retry_test:retry") == 1
        
        # move_retry 移回主队列
        q.move_retry()
        assert r.llen("testns:retry_test") == 1

    def test_dlq(self, redis_url, r):
        q = SmartQueue(redis_url, "dlq_test", namespace="testns", max_retry=1)
        task_id = q.push({"action": "test"})
        
        # 第一次失败，进入 retry
        payload, raw = q.pop()
        q.fail(raw, "test error")
        
        # 第二次 pop 会从 retry 队列取出
        payload2, raw2 = q.pop(timeout=1)
        # 第二次失败，进入 DLQ
        if raw2:
            q.fail(raw2, "test error")
        
        assert r.llen("testns:dlq_test:dlq") >= 1

    def test_delay(self, redis_url, r):
        q = SmartQueue(redis_url, "delay_test", namespace="testns")
        # 创建一个延迟任务
        import time
        now = time.time()
        msg = '{"task_id":"test","payload":"{}"}'
        r.zadd("testns:delay_test:delay", {msg: now - 1})  # 已过期
        
        assert r.zcard("testns:delay_test:delay") == 1
        
        # move_delay 移到主队列
        count = q.move_delay()
        assert count == 1
        assert r.llen("testns:delay_test") == 1

    def test_ttl(self, redis_url, r):
        q = SmartQueue(redis_url, "ttl_test", namespace="testns", ttl_days=15)
        q.push({"action": "test"})
        
        # 检查历史索引的 TTL
        ttl = r.ttl("qtask:hist:testns:ttl_test")
        assert ttl > 0

    def test_history(self, redis_url, r):
        q = SmartQueue(redis_url, "history_test", namespace="testns")
        task_id = q.push({"action": "test_action", "data": "hello"})
        
        history = q.history.list(limit=10)
        assert len(history) >= 1
        assert history[0]["action"] == "test_action"
        assert history[0]["status"] == "pending"

    def test_clean_expired(self, redis_url, r):
        q = SmartQueue(redis_url, "clean_test", namespace="testns")
        q.push({"action": "test"})
        
        count = q.history.clean_expired(ttl_seconds=0)
        assert count >= 1
        
        history = q.history.list(limit=10)
        assert len(history) == 0
