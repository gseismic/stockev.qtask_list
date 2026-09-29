import pytest
import time
import threading
import redis
from datetime import datetime, timedelta, timezone
from qtask_list import Worker, SmartQueue, TaskSpec


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
        # V2 异常自动进入 delay retry_wait，业务 payload 不增加 _retry 字段。
        q = SmartQueue(redis_url, "exception_test", namespace="testns", retry_backoff_base=0)
        
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
        
        # 任务进入 retry_wait
        assert r.zcard("testns:exception_test:delay") == 1
        assert r.llen("testns:exception_test:retry") == 0

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

    def test_stop_before_run_is_not_lost(self, redis_url, r):
        worker = Worker(redis_url, "stopped_before_start", namespace="testns")
        called = threading.Event()

        @worker.on("test")
        def handler(payload):
            called.set()

        task_id = worker.queue.enqueue(TaskSpec(action="test", payload={})).task_id
        worker.stop(reason="before_start")
        worker.run()
        assert not called.is_set()
        assert r.llen(worker.queue.queue) == 1
        assert r.hget(f"qtask:task:{task_id}", "attempt") == "0"

    def test_stop_during_handler_lookup_returns_unadmitted_claim(self, redis_url, r):
        # 领取后到 handler 准入前的竞态，也必须退回任务而不开始调用。
        worker = Worker(redis_url, "stop_during_lookup", namespace="testns", worker_id="stop-lookup")
        entered = threading.Event()
        proceed = threading.Event()
        handler_called = threading.Event()

        @worker.on("test")
        def handler(payload):
            handler_called.set()

        class PausingHandlers(dict):
            def get(self, key, default=None):
                entered.set()
                if not proceed.wait(5):
                    raise TimeoutError("test did not release handler lookup")
                return super().get(key, default)

        worker.handlers = PausingHandlers(worker.handlers)
        task_id = worker.queue.enqueue(TaskSpec(action="test", payload={})).task_id
        runner = threading.Thread(target=worker.run)
        runner.start()
        try:
            assert entered.wait(5)
            worker.stop(reason="test")
            proceed.set()
            runner.join(5)
            assert not runner.is_alive()
            assert not handler_called.is_set()
            assert r.hget(f"qtask:task:{task_id}", "attempt") == "0"
            assert r.llen(worker.queue.queue) == 1
            assert r.llen(worker.queue.processing) == 0
        finally:
            proceed.set()
            worker.stop(reason="test_cleanup")
            runner.join(5)

    def test_stop_during_blocking_pop_returns_claim_without_running_handler(self, redis_url, r):
        # 停机发生在阻塞领取期间：保持心跳，退回 claim，并恢复 attempt 与租约。
        worker = Worker(
            redis_url, "stop_during_pop", namespace="testns",
            worker_id="stop-during-pop", heartbeat_ttl=2,
        )
        entered = threading.Event()
        proceed = threading.Event()
        handler_called = threading.Event()
        original_pop = worker.queue.pop_claim

        def held_pop(*args, **kwargs):
            entered.set()
            if not proceed.wait(6):
                raise TimeoutError("test did not release pop")
            return original_pop(*args, **kwargs)

        worker.queue.pop_claim = held_pop

        @worker.on("test")
        def handler(payload):
            handler_called.set()

        runner = threading.Thread(target=worker.run)
        runner.start()
        try:
            assert entered.wait(5)
            worker.stop(reason="test")
            task_id = worker.queue.enqueue(TaskSpec(
                action="test", payload={"value": 1}, concurrency_key="same",
                start_deadline_at=datetime.now(timezone.utc) + timedelta(minutes=1),
            )).task_id
            time.sleep(2.2)
            assert r.exists(worker._heartbeat_key) == 1
            proceed.set()
            runner.join(5)
            assert not runner.is_alive()
            assert not handler_called.is_set()
            assert r.llen(worker.queue.queue) == 1
            assert r.llen(worker.queue.processing) == 0
            assert r.hget(f"qtask:task:{task_id}", "attempt") == "0"
            assert r.zscore(worker.queue.deadline_key, task_id) is not None
            assert r.exists(worker.queue._lease_key("same")) == 0
        finally:
            proceed.set()
            runner.join(5)

    def test_stop_keeps_heartbeat_until_running_handler_finishes(self, redis_url, r):
        # 已进入 handler 的任务在 drain 期间持续受到 heartbeat 保护。
        worker = Worker(
            redis_url, "running_drain", namespace="testns",
            worker_id="running-drain", heartbeat_ttl=2,
        )
        started = threading.Event()
        finish = threading.Event()

        @worker.on("test")
        def handler(payload):
            started.set()
            assert finish.wait(6)

        task_id = worker.queue.enqueue(TaskSpec(action="test", payload={})).task_id
        runner = threading.Thread(target=worker.run)
        runner.start()
        try:
            assert started.wait(5)
            worker.stop(reason="test")
            time.sleep(2.2)
            assert r.exists(worker._heartbeat_key) == 1
            finish.set()
            runner.join(5)
            assert not runner.is_alive()
            assert r.hget(f"qtask:task:{task_id}", "outcome") == "completed"
        finally:
            finish.set()
            worker.stop(reason="test_cleanup")
            runner.join(5)

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
        with pytest.raises(ValueError, match="action"):
            worker.queue.push({})

        assert r.llen("testns:empty_payload:processing:" + worker.worker_id) == 0
        assert r.llen("testns:empty_payload:dlq") == 0

    def test_worker_stop_event_remains_set_during_drain(self, redis_url, r):
        worker = Worker(redis_url, "drain_stop", namespace="testns", max_workers=2)
        worker.running = True
        worker._draining = False
        worker.executor = object()

        worker.stop(reason="test")

        assert worker._shutdown_event.is_set()
        assert worker._maintenance_wakeup.is_set()
        assert worker._draining is True

    def test_worker_uses_worker_specific_processing_key(self, redis_url):
        worker = Worker(redis_url, "specific_processing", namespace="testns", worker_id="worker-a")

        assert worker.queue.processing == "testns:specific_processing:processing:worker-a"
