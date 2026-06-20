import time
import signal
import threading
import os
import uuid
from typing import Any, Callable, Dict, Optional
from loguru import logger
from concurrent.futures import ThreadPoolExecutor

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
        redis_client: Optional[Any] = None,
        worker_id: Optional[str] = None,
        heartbeat_ttl: int = 120,
    ):
        self.redis_url = redis_url
        self.worker_id = (worker_id or f"{os.getpid()}-{uuid.uuid4().hex}").replace(":", "_")
        self.heartbeat_ttl = max(heartbeat_ttl, 2)
        self.heartbeat_interval = max(1, min(30, self.heartbeat_ttl // 3))

        base = f"{namespace}:{queue_name}" if namespace else queue_name
        processing_key = f"{base}:processing:{self.worker_id}"

        self.queue = SmartQueue(
            redis_url,
            queue_name,
            namespace=namespace,
            storage=storage,
            max_retry=max_retry,
            redis_client=redis_client,
            processing_key=processing_key,
        )

        self.result_queue = result_queue
        self._heartbeat_prefix = f"{self.queue.base}:worker:"
        self._heartbeat_key = f"{self._heartbeat_prefix}{self.worker_id}"

        self.handlers: Dict[str, Callable] = {}

        self.max_workers = max_workers
        self.running = False

        self.executor: Optional[ThreadPoolExecutor] = None
        # 用于控制线程池积压，防止任务无限排队导致内存爆炸
        self._semaphore = threading.Semaphore(max_workers * 2) if max_workers > 1 else None

        self._shutdown_event = threading.Event()
        self._draining = False
        self.maintenance_interval = maintenance_interval

    def _refresh_heartbeat(self):
        self.queue.r.set(self._heartbeat_key, str(time.time()), ex=self.heartbeat_ttl)

    def _cleanup_worker_state(self):
        if self.queue.r.llen(self.queue.processing) == 0:
            self.queue.r.delete(self.queue.processing)
        self.queue.r.delete(self._heartbeat_key)

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

    def _process_task(self, payload: Dict[str, Any], raw_msg: str):
        """处理单个任务"""
        action = payload.get("action")

        if not action:
            logger.warning("Task has no action, skipping")
            self.queue.fail(raw_msg, "no action")
            return

        handler = self.handlers.get(action)

        if not handler:
            logger.warning(f"No handler for action: {action}")
            self.queue.fail(raw_msg, f"unknown action: {action}")
            return

        try:
            result = handler(payload)

            if self.result_queue and result:
                self.result_queue.push(result)

            self.queue.ack(raw_msg)

        except Exception as e:
            logger.error(f"Handler error for {action}: {e}")
            self.queue.fail(raw_msg, str(e))

    def _maintenance_loop(self):
        """定期清理和归档的维护线程"""
        from .archiver import ArchiveManager, Monitor

        archiver = ArchiveManager(self.redis_url)
        monitor = Monitor(self.queue.r, threshold_mb=512)  # 可配置阈值

        logger.info("Maintenance thread started")

        last_maintenance = 0
        while self.running or self._draining:
            try:
                self._refresh_heartbeat()

                if self.running:
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
            stop_wait = min(60, self.maintenance_interval, self.heartbeat_interval)
            if self._shutdown_event.wait(stop_wait):
                if not self._draining:
                    break
                self._shutdown_event.clear()

        logger.info("Maintenance thread stopped")

    def _poll_once(self) -> bool:
        self._refresh_heartbeat()
        self.queue.move_retry()
        self.queue.move_delay()

        # pop 阻塞超时设置为 2 秒，以便能响应停止信号
        payload, raw = self.queue.pop(timeout=2)

        if raw is None or payload is None:
            return False

        if self.max_workers > 1:
            # 获取许可
            if self._semaphore is None or self.executor is None:
                raise RuntimeError("Thread pool is not initialized")
            self._semaphore.acquire()
            self.executor.submit(self._process_task_with_semaphore, payload, raw)
        else:
            self._process_task(payload, raw)

        return True

    def _worker_loop(self):
        """Worker 主循环"""
        logger.info(f"Worker started, listening on {self.queue.base} as {self.worker_id}")

        while self.running:
            try:
                self._poll_once()
            except Exception as e:
                logger.error(f"Worker loop error: {e}")

        logger.info("Worker stopped")

    def run(self):
        """启动 Worker"""
        self.running = True
        self._shutdown_event.clear()

        # 仅在主线程且在 run 时注册信号
        if threading.current_thread() is threading.main_thread():
            try:
                signal.signal(signal.SIGINT, self._signal_handler)
                signal.signal(signal.SIGTERM, self._signal_handler)
            except ValueError:
                logger.warning("Failed to register signals (not in main thread?)")

        recovered = self.queue.recover_stale_processing(self._heartbeat_prefix)
        if recovered:
            logger.info(f"Recovered {recovered} tasks from stale processing queues")
        self._refresh_heartbeat()

        # 启动维护线程
        maintenance_thread = threading.Thread(target=self._maintenance_loop, daemon=True)
        maintenance_thread.start()

        if self.max_workers > 1:
            self.executor = ThreadPoolExecutor(max_workers=self.max_workers)

        try:
            self._worker_loop()
        finally:
            self._draining = self.executor is not None
            self.stop()
            if self._draining:
                self._shutdown_event.clear()
                self.executor.shutdown(wait=True)
                self._draining = False
                self._shutdown_event.set()
            maintenance_thread.join(timeout=5)
            self._cleanup_worker_state()

    def stop(self):
        """停止 Worker"""
        if not self.running:
            return

        self.running = False
        self._shutdown_event.set()
