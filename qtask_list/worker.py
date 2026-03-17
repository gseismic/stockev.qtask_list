import time
import signal
import threading
import redis
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
        maintenance_interval: int = 1800,  # 30 mins
        redis_client: Optional[redis.Redis] = None,
    ):
        self.redis_url = redis_url

        self.queue = SmartQueue(
            redis_url,
            queue_name,
            namespace=namespace,
            storage=storage,
            max_retry=max_retry,
            redis_client=redis_client,
        )

        self.result_queue = result_queue

        self.handlers: Dict[str, Callable] = {}

        self.max_workers = max_workers
        self.running = False

        self.executor: Optional[ThreadPoolExecutor] = None
        # 用于控制线程池积压，防止任务无限排队导致内存爆炸
        self._semaphore = threading.Semaphore(max_workers * 2) if max_workers > 1 else None

        self._shutdown_event = threading.Event()
        self.maintenance_interval = maintenance_interval

    def _signal_handler(self, signum, frame):
        logger.info(f"Received signal {signum}, stopping worker...")
        self.stop()

    def on(self, action: str):
        """注册任务处理器"""

        def decorator(fn: Callable[[dict], Optional[dict]]):
            self.handlers[action] = fn
            return fn

        return decorator

    def _process_task_with_semaphore(self, payload: dict, raw_msg: str):
        """带信号量的处理器包装"""
        try:
            self._process_task(payload, raw_msg)
        finally:
            if self._semaphore:
                self._semaphore.release()

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

    def _maintenance_loop(self):
        """定期清理和归档的维护线程"""
        from .archiver import ArchiveManager, Monitor

        archiver = ArchiveManager(self.redis_url)
        monitor = Monitor(self.queue.r, threshold_mb=512)  # 可配置阈值

        logger.info("Maintenance thread started")

        last_maintenance = 0
        while self.running:
            try:
                # 检查内存
                monitor.check_health()

                # 定期归档
                now = time.time()
                if now - last_maintenance > self.maintenance_interval:
                    count = archiver.archive_to_sqlite(self.queue.base, days_ago=1)
                    if count > 0:
                        logger.info(f"Archived {count} tasks to SQLite")
                    last_maintenance = now

            except Exception as e:
                logger.error(f"Maintenance error: {e}")

            # 等待或停止
            stop_wait = min(60, self.maintenance_interval)
            if self._shutdown_event.wait(stop_wait):
                break

        logger.info("Maintenance thread stopped")

    def _worker_loop(self):
        """Worker 主循环"""
        logger.info(f"Worker started, listening on {self.queue.base}")

        while self.running:
            try:
                self.queue.move_retry()
                self.queue.move_delay()

                # pop 阻塞超时设置为 2 秒，以便能响应停止信号
                payload, raw = self.queue.pop(timeout=2)

                if not payload:
                    continue

                if self.max_workers > 1:
                    # 获取许可
                    self._semaphore.acquire()
                    self.executor.submit(self._process_task_with_semaphore, payload, raw)
                else:
                    self._process_task(payload, raw)

            except Exception as e:
                logger.error(f"Worker loop error: {e}")

        logger.info("Worker stopped")

    def run(self):
        """启动 Worker"""
        self.running = True

        # 仅在主线程且在 run 时注册信号
        if threading.current_thread() is threading.main_thread():
            try:
                signal.signal(signal.SIGINT, self._signal_handler)
                signal.signal(signal.SIGTERM, self._signal_handler)
            except ValueError:
                logger.warning("Failed to register signals (not in main thread?)")

        self.queue.recover()

        # 启动维护线程
        maintenance_thread = threading.Thread(target=self._maintenance_loop, daemon=True)
        maintenance_thread.start()

        if self.max_workers > 1:
            self.executor = ThreadPoolExecutor(max_workers=self.max_workers)

        try:
            self._worker_loop()
        finally:
            self.stop()
            if self.executor:
                self.executor.shutdown(wait=True)

    def stop(self):
        """停止 Worker"""
        if not self.running:
            return

        self.running = False
        self._shutdown_event.set()
