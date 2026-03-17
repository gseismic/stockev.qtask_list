import json
import time
import uuid
import redis
from typing import Optional, Any, Dict, List
from loguru import logger

from .history import TaskHistory
from .storage import RemoteStorage


class SmartQueue:
    """
    基于 Redis List 的智能任务队列

    队列结构:
        {namespace}:{queue_name}         - 主队列
        {namespace}:{queue_name}:processing - 处理中
        {namespace}:{queue_name}:retry   - 重试队列
        {namespace}:{queue_name}:dlq     - 死信队列
        {namespace}:{queue_name}:delay   - 延迟队列 (sorted set)
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        queue_name: str = "",
        namespace: Optional[str] = None,
        storage: Optional[RemoteStorage] = None,
        large_threshold: int = 50 * 1024,
        max_retry: int = 3,
        ttl_days: int = 15,
        redis_client: Optional[redis.Redis] = None,
    ):
        if redis_client is not None:
            self.r = redis_client
        elif redis_url:
            self.r = redis.from_url(redis_url, decode_responses=True)
        else:
            raise ValueError("Either redis_url or redis_client must be provided")

        self.namespace = namespace or ""
        self.base = f"{self.namespace}:{queue_name}" if self.namespace else queue_name

        self.queue = self.base
        self.processing = f"{self.base}:processing"
        self.retry = f"{self.base}:retry"
        self.dlq = f"{self.base}:dlq"
        self.delay = f"{self.base}:delay"

        self.storage = storage
        self.large_threshold = large_threshold
        self.max_retry = max_retry

        self.history = TaskHistory(redis_client=self.r, queue_name=self.base, ttl_days=ttl_days)

    # ==================== Push ====================

    def push(self, payload: Dict[str, Any], delay_seconds: int = 0) -> str:
        """
        推送任务到队列
        """
        try:
            task_id = str(uuid.uuid4())

            original_action = payload.get("action")
            payload_json = json.dumps(payload)
            data = payload_json.encode("utf-8")

            if self.storage and len(data) > self.large_threshold:
                key = self.storage.save_bytes(data)
                payload = {"_large": True, "key": key}
                payload_json = json.dumps(payload)

            msg = json.dumps({"task_id": task_id, "payload": payload_json})

            self.history.record(task_id, {"action": original_action, "status": "pending"})

            if delay_seconds > 0:
                self._push_delay(msg, delay_seconds)
            else:
                self.r.lpush(self.queue, msg)

            return task_id
        except Exception as e:
            logger.error(f"Push failed: {e}")
            raise

    def push_batch(self, payloads: List[Dict[str, Any]]) -> List[str]:
        """批量推送任务"""
        task_ids = []
        pipe = self.r.pipeline()

        for payload in payloads:
            task_id = str(uuid.uuid4())
            task_ids.append(task_id)

            original_action = payload.get("action")
            payload_json = json.dumps(payload)
            data = payload_json.encode("utf-8")

            if self.storage and len(data) > self.large_threshold:
                key = self.storage.save_bytes(data)
                payload = {"_large": True, "key": key}
                payload_json = json.dumps(payload)

            msg = json.dumps({"task_id": task_id, "payload": payload_json})

            pipe.lpush(self.queue, msg)

            task_key = f"qtask:task:{task_id}"
            hist_data = {
                "action": original_action,
                "status": "pending",
                "task_id": task_id,
                "created_at": time.time(),
            }
            hist_mapping = {
                k: json.dumps(v) if isinstance(v, (dict, list)) else v for k, v in hist_data.items()
            }
            pipe.hset(task_key, mapping=hist_mapping)
            pipe.expire(task_key, self.history.ttl_seconds)
            pipe.zadd(self.history.idx_key, {task_id: time.time()})
            pipe.expire(self.history.idx_key, self.history.ttl_seconds)

        pipe.execute()
        return task_ids

    def _push_delay(self, msg: str, delay_seconds: int):
        """推送到延迟队列"""
        ts = time.time() + delay_seconds
        self.r.zadd(self.delay, {msg: ts})

    # ==================== Pop ====================

    def pop(self, timeout: int = 10) -> Optional[tuple]:
        """
        从队列获取任务
        """
        try:
            msg = self.r.brpoplpush(self.queue, self.processing, timeout)

            if not msg:
                return None, None

            data = json.loads(msg)
            payload = json.loads(data["payload"])

            if payload.get("_large"):
                raw = self.storage.load(payload["key"])
                payload = json.loads(raw)

            return payload, msg
        except Exception as e:
            logger.error(f"Pop failed: {e}")
            return None, None

    def pop_no_wait(self) -> Optional[tuple]:
        """非阻塞 pop"""
        msg = self.r.rpoplpush(self.queue, self.processing)
        if not msg:
            return None, None

        data = json.loads(msg)
        payload = json.loads(data["payload"])

        if payload.get("_large"):
            raw = self.storage.load(payload["key"])
            payload = json.loads(raw)

        return payload, msg

    # ==================== Ack ====================

    def ack(self, raw_msg: str):
        """确认任务完成"""
        self.r.lrem(self.processing, 1, raw_msg)

        data = json.loads(raw_msg)
        self.history.update(data["task_id"], {"status": "completed"})

    # ==================== Fail ====================

    def fail(self, raw_msg: str, reason: str = ""):
        """标记任务失败"""
        data = json.loads(raw_msg)
        payload = json.loads(data["payload"])

        retry = payload.get("_retry", 0) + 1
        payload["_retry"] = retry

        self.r.lrem(self.processing, 1, raw_msg)

        if retry >= self.max_retry:
            self.r.lpush(self.dlq, raw_msg)
            self.history.update(data["task_id"], {"status": "failed", "reason": reason})
        else:
            new_msg = json.dumps({"task_id": data["task_id"], "payload": json.dumps(payload)})
            self.r.rpush(self.retry, new_msg)
            self.history.update(data["task_id"], {"status": "retry"})

    # ==================== Retry ====================

    def move_retry(self) -> int:
        """
        将重试队列中的任务移回主队列

        Returns:
            移动的任务数
        """
        count = 0
        while True:
            msg = self.r.rpoplpush(self.retry, self.queue)
            if not msg:
                break
            count += 1
        return count

    # ==================== Delay ====================

    def move_delay(self) -> int:
        """将延迟队列中已到期的任务移回主队列（原子操作）"""
        now = time.time()

        lua_script = """
        local count = 0
        while true do
            local tasks = redis.call('ZRANGEBYSCORE', KEYS[1], 0, ARGV[1], 'LIMIT', 0, 1)
            if #tasks == 0 then
                break
            end
            local task = tasks[1]
            redis.call('LPUSH', KEYS[2], task)
            redis.call('ZREM', KEYS[1], task)
            count = count + 1
        end
        return count
        """
        result = self.r.eval(lua_script, 2, self.delay, self.queue, now)
        return result if result else 0

    # ==================== Recovery ====================

    def recover(self) -> int:
        """
        Crash recovery: 将 processing 队列中的任务移回主队列

        Returns:
            恢复的任务数
        """
        count = 0
        while True:
            msg = self.r.rpoplpush(self.processing, self.queue)
            if not msg:
                break
            count += 1
        return count

    # ==================== Queue Management ====================

    def size(self) -> int:
        """主队列大小"""
        return self.r.llen(self.queue)

    def processing_size(self) -> int:
        """处理中队列大小"""
        return self.r.llen(self.processing)

    def retry_size(self) -> int:
        """重试队列大小"""
        return self.r.llen(self.retry)

    def dlq_size(self) -> int:
        """死信队列大小"""
        return self.r.llen(self.dlq)

    def delay_size(self) -> int:
        """延迟队列大小"""
        return self.r.zcard(self.delay)

    def get_stats(self) -> dict:
        """获取队列统计"""
        return {
            "queue": self.size(),
            "processing": self.processing_size(),
            "retry": self.retry_size(),
            "dlq": self.dlq_size(),
            "delay": self.delay_size(),
        }

    def clear(self, include_dlq: bool = True):
        """清空队列"""
        self.r.delete(self.queue)
        self.r.delete(self.processing)
        self.r.delete(self.retry)
        self.r.delete(self.delay)
        if include_dlq:
            self.r.delete(self.dlq)

    def requeue_dlq(self) -> int:
        """将 DLQ 中的任务重新入队"""
        count = 0
        while True:
            msg = self.r.rpoplpush(self.dlq, self.queue)
            if not msg:
                break
            count += 1
        return count
