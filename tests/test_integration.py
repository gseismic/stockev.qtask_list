import pytest
import time
import json
from qtask_list import SmartQueue


@pytest.fixture
def redis_url():
    return "redis://localhost:6379/0"


@pytest.fixture
def r(redis_url):
    import redis

    client = redis.from_url(redis_url, decode_responses=True)
    yield client
    keys = client.keys("testns:*")
    for k in keys:
        client.delete(k)
    keys = client.keys("integration:*")
    for k in keys:
        client.delete(k)


class TestIntegration:
    def test_full_pipeline(self, redis_url, r):
        """测试完整的 pipeline: fetch -> store"""
        # 1. 推送 fetch 任务
        fetch_q = SmartQueue(redis_url, "fetch", namespace="integration")
        store_q = SmartQueue(redis_url, "store", namespace="integration")

        task_id = fetch_q.push({"action": "fetch_data", "symbol": "AAPL"})

        # 2. fetch worker 处理
        payload, raw = fetch_q.pop()
        assert payload["symbol"] == "AAPL"

        # 3. 返回结果推送到 store 队列
        result = {"action": "store_data", "symbol": "AAPL", "price": 150.0}

        # 4. store worker 处理
        store_q.push(result)

        stored_payload, stored_raw = store_q.pop()
        assert stored_payload["symbol"] == "AAPL"
        assert stored_payload["price"] == 150.0

        # 5. 确认完成
        store_q.ack(stored_raw)

        # 验证队列清空
        assert r.llen("integration:fetch") == 0
        assert r.llen("integration:store") == 0

    def test_multiple_workers(self, redis_url, r):
        """测试多个 worker 并发处理"""
        q = SmartQueue(redis_url, "parallel", namespace="integration")

        # 推送多个任务
        for i in range(10):
            q.push({"action": "process", "index": i})

        # 消费任务
        processed = 0
        while True:
            payload, raw = q.pop(timeout=1)
            if not payload:
                break
            q.ack(raw)
            processed += 1

        assert processed == 10

    def test_retry_then_success(self, redis_url, r):
        """测试重试后成功"""
        q = SmartQueue(redis_url, "retry_success", namespace="integration", max_retry=3)

        q.push({"action": "unreliable", "fail_count": 0})

        # 第一次失败
        payload, raw = q.pop()
        q.fail(raw, "temporary error")

        # move_retry 移回主队列
        q.move_retry()

        # 第二次成功
        payload2, raw2 = q.pop()
        q.ack(raw2)

        # 验证
        history = q.history.list(limit=1)
        assert len(history) >= 1
        # 状态应该是 retry 然后 completed

    def test_delay_pipeline(self, redis_url, r):
        """测试延迟任务 pipeline"""
        q = SmartQueue(redis_url, "delay_pipeline", namespace="integration")

        # 推送延迟任务
        q.push({"action": "delayed"}, delay_seconds=0)

        # 立即移动到主队列
        q.move_delay()

        # 消费
        payload, raw = q.pop()
        assert payload["action"] == "delayed"
        q.ack(raw)


class TestEdgeCases:
    def test_empty_payload(self, redis_url, r):
        """测试空 payload"""
        q = SmartQueue(redis_url, "empty", namespace="integration")

        task_id = q.push({})
        assert task_id is not None

    def test_large_payload(self, redis_url, r):
        """测试大 payload (超过阈值会触发 storage)"""
        # 使用小阈值测试
        q = SmartQueue(redis_url, "large", namespace="integration", large_threshold=100)

        # 创建大于 100 字节的数据
        large_data = {"data": "x" * 200}

        task_id = q.push(large_data)
        assert task_id is not None

    def test_special_characters_in_payload(self, redis_url, r):
        """测试 payload 中的特殊字符"""
        q = SmartQueue(redis_url, "special", namespace="integration")

        q.push({"action": "test", "text": "你好世界! @#$%^&*()", "unicode": "🚀🌟💻"})

        payload, raw = q.pop()
        assert payload["text"] == "你好世界! @#$%^&*()"
        assert payload["unicode"] == "🚀🌟💻"

    def test_pop_timeout(self, redis_url, r):
        """测试 pop 超时"""
        q = SmartQueue(redis_url, "timeout", namespace="integration")

        # 队列为空时 pop 应该返回 None
        payload, raw = q.pop(timeout=1)
        assert payload is None
        assert raw is None

    def test_queue_stats(self, redis_url, r):
        """测试队列统计"""
        q = SmartQueue(redis_url, "stats", namespace="integration")

        q.push({"action": "test1"})
        q.push({"action": "test2"})
        q.push({"action": "test3"})

        stats = q.get_stats()
        assert stats["queue"] == 3
        assert stats["processing"] == 0

        # pop 后
        q.pop()
        stats = q.get_stats()
        assert stats["queue"] == 2
        assert stats["processing"] == 1

    def test_queue_without_namespace(self, redis_url, r):
        """测试无命名空间"""
        r.delete("no_namespace")

        q = SmartQueue(redis_url, "no_namespace")

        q.push({"action": "test"})

        assert r.llen("no_namespace") == 1

    def test_ack_nonexistent(self, redis_url, r):
        """测试 ack 不存在的消息"""
        q = SmartQueue(redis_url, "ack_test", namespace="integration")

        # ack 一个不存在的消息应该不会报错
        fake_msg = json.dumps({"task_id": "nonexistent", "payload": "{}"})

        # 不应该抛出异常
        q.ack(fake_msg)

    def test_fail_nonexistent(self, redis_url, r):
        """测试 fail 不存在的消息"""
        q = SmartQueue(redis_url, "fail_test", namespace="integration")

        fake_msg = json.dumps({"task_id": "nonexistent", "payload": "{}"})

        # 不应该抛出异常
        q.fail(fake_msg, "test error")


class TestHistory:
    def test_history_update(self, redis_url, r):
        """测试历史更新"""
        q = SmartQueue(redis_url, "hist_update", namespace="integration")

        task_id = q.push({"action": "test"})

        # 更新状态
        q.history.update(task_id, {"status": "processing"})

        # 获取详情
        detail = q.history.get(task_id)
        assert detail["status"] == "processing"

    def test_history_clear(self, redis_url, r):
        """测试清空历史"""
        q = SmartQueue(redis_url, "hist_clear", namespace="integration")

        q.push({"action": "test1"})
        q.push({"action": "test2"})

        # 清空
        q.history.clear()

        history = q.history.list()
        assert len(history) == 0


class TestConfiguration:
    def test_custom_max_retry(self, redis_url, r):
        """测试自定义重试次数"""
        q = SmartQueue(redis_url, "custom_retry", namespace="integration", max_retry=5)

        assert q.max_retry == 5

    def test_custom_ttl(self, redis_url, r):
        """测试自定义 TTL"""
        q = SmartQueue(redis_url, "custom_ttl", namespace="integration", ttl_days=7)

        assert q.history.ttl_seconds == 7 * 86400
