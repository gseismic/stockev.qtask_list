import time
import signal
import threading
from typing import Callable, Optional, Dict, Any
from loguru import logger
from concurrent.futures import ThreadPoolExecutor, Future

from .queue import SmartQueue
from .storage import RemoteStorage


class Worker:
    """
    任务处理器 Worker
    
    支持:
    - 事件驱动 handler 注册
    - 多线程并发处理
    - Crash recovery
    - 自动重试
    """

    def __init__(
        self,
        redis_url: str,
        queue_name: str,
        namespace: Optional[str] = None,
        result_queue: Optional[SmartQueue] = None,
        storage: Optional[RemoteStorage] = None,
        max_workers: int = 1,
        max_retry: int = 3,
    ):
        self.redis_url = redis_url

        self.queue = SmartQueue(
            redis_url,
            queue_name,
            namespace=namespace,
            storage=storage,
            max_retry=max_retry,
        )

        self.result_queue = result_queue

        self.handlers: Dict[str, Callable] = {}

        self.max_workers = max_workers
        self.running = False

        self.executor: Optional[ThreadPoolExecutor] = None
        self._shutdown_event = threading.Event()

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        logger.info("Received shutdown signal, stopping worker...")
        self.stop()

    def on(self, action: str):
        """注册任务处理器"""
        def decorator(fn: Callable[[dict], Optional[dict]]):
            self.handlers[action] = fn
            return fn
        return decorator

    def _process_task(self, payload: dict, raw_msg: str) -> bool:
        """处理单个任务"""
        action = payload.get("action")

        if not action:
            logger.warning("Task has no action, skipping")
            self.queue.fail(raw_msg, "no action")
            return False

        handler = self.handlers.get(action)

        if not handler:
            logger.warning(f"No handler for action: {action}")
            self.queue.fail(raw_msg, f"unknown action: {action}")
            return False

        try:
            result = handler(payload)

            if self.result_queue and result:
                self.result_queue.push(result)

            self.queue.ack(raw_msg)
            return True

        except Exception as e:
            logger.error(f"Handler error for {action}: {e}")
            self.queue.fail(raw_msg, str(e))
            return False

    def _worker_loop(self):
        """Worker 主循环"""
        logger.info(f"Worker started, listening on {self.queue.base}")

        while self.running:
            try:
                self.queue.move_retry()
                self.queue.move_delay()

                payload, raw = self.queue.pop(timeout=5)

                if not payload:
                    continue

                if self.max_workers > 1:
                    self.executor.submit(self._process_task, payload, raw)
                else:
                    self._process_task(payload, raw)

            except Exception as e:
                logger.error(f"Worker loop error: {e}")

        logger.info("Worker stopped")

    def run(self):
        """启动 Worker"""
        self.running = True

        self.queue.recover()

        if self.max_workers > 1:
            self.executor = ThreadPoolExecutor(max_workers=self.max_workers)

        try:
            self._worker_loop()
        finally:
            if self.executor:
                self.executor.shutdown(wait=True)

    def stop(self):
        """停止 Worker"""
        self.running = False
        self._shutdown_event.set()
