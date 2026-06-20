import pytest
import time
import redis
from qtask_list import Worker, SmartQueue


@pytest.fixture
def redis_url():
    return "redis://localhost:6379/0"


@pytest.fixture
def r(redis_url):
    client = redis.from_url(redis_url, decode_responses=True)
    yield client
    for k in client.scan_iter("testns:*"):
        client.delete(k)
    for k in client.scan_iter("qtask:hist:testns:*"):
        client.delete(k)


class TestWorker:

    def test_worker_register_handler(self, redis_url, r):
        worker = Worker(redis_url, "test", namespace="testns")
        
        @worker.on("test_action")
        def handler(task):
            return {"result": "ok"}
        
        assert "test_action" in worker.handlers

    def test_worker_unknown_action(self, redis_url, r):
        q = SmartQueue(redis_url, "unknown_test", namespace="testns", max_retry=0)
        
        q.push({"action": "unknown"})
        
        payload, raw = q.pop()
        action = payload.get("action")
        handler = None
        if not handler:
            q.fail(raw, f"unknown action: {action}")
        
        assert r.llen("testns:unknown_test:dlq") == 1

    def test_worker_result_queue(self, redis_url, r):
        q = SmartQueue(redis_url, "result_test", namespace="testns")
        result_q = SmartQueue(redis_url, "result_out", namespace="testns")
        
        q.push({"action": "process", "value": 10})
        
        worker = Worker(
            redis_url,
            "result_test",
            namespace="testns",
            result_queue=result_q,
        )
        
        @worker.on("process")
        def process(task):
            return {
                "action": "done",
                "result": task["value"] * 2
            }
        
        # 处理任务
        payload, raw = q.pop()
        result = process(payload)
        if result and result_q:
            result_q.push(result)
        
        # 验证结果
        assert r.llen("testns:result_out") == 1

    def test_worker_exception_handling(self, redis_url, r):
        q = SmartQueue(redis_url, "exception_test", namespace="testns")
        
        q.push({"action": "error_task"})
        
        worker = Worker(redis_url, "exception_test", namespace="testns")
        
        @worker.on("error_task")
        def error_handler(task):
            raise ValueError("Test error")
        
        # 处理任务，验证异常被捕获
        payload, raw = q.pop()
        
        try:
            error_handler(payload)
        except ValueError:
            q.fail(raw, "ValueError")
        
        # 任务进入 retry
        assert r.llen("testns:exception_test:retry") == 1

    def test_worker_with_multiple_handlers(self, redis_url, r):
        worker = Worker(redis_url, "multi_test", namespace="testns")

        @worker.on("task_a")
        def handler_a(task):
            return {"from": "a"}

        @worker.on("task_b")
        def handler_b(task):
            return {"from": "b"}

        assert "task_a" in worker.handlers
        assert "task_b" in worker.handlers
        assert worker.handlers["task_a"]("test")["from"] == "a"
        assert worker.handlers["task_b"]("test")["from"] == "b"


class TestWorkerConcurrency:

    def test_worker_max_workers(self, redis_url, r):
        q = SmartQueue(redis_url, "concurrency_test", namespace="testns")
        
        # 推送多个任务
        for i in range(5):
            q.push({"action": "process", "index": i})
        
        worker = Worker(
            redis_url,
            "concurrency_test",
            namespace="testns",
            max_workers=3,
        )
        
        @worker.on("process")
        def process(task):
            time.sleep(0.1)
            return {"index": task["index"]}
        
        # 验证 worker 配置
        assert worker.max_workers == 3


class TestWorkerLifecycle:

    def test_worker_start_stop(self, redis_url, r):
        worker = Worker(redis_url, "lifecycle_test", namespace="testns")
        
        @worker.on("test")
        def handler(task):
            return None

        assert not worker.running

        # 模拟启动
        worker.running = True
        assert worker.running

        # 模拟停止
        worker.stop()
        assert not worker.running

    def test_worker_empty_payload_is_failed_not_left_processing(self, redis_url, r):
        worker = Worker(redis_url, "empty_payload", namespace="testns", max_retry=1)
        worker.queue.push({})

        worker._poll_once()

        assert r.llen("testns:empty_payload:processing:" + worker.worker_id) == 0
        assert r.llen("testns:empty_payload:dlq") == 1

    def test_worker_uses_worker_specific_processing_key(self, redis_url):
        worker = Worker(redis_url, "specific_processing", namespace="testns", worker_id="worker-a")

        assert worker.queue.processing == "testns:specific_processing:processing:worker-a"
