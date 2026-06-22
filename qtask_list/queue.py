import base64
import json
import time
import uuid
from typing import Any, Dict, List, Optional, cast

import orjson
import redis
import zstandard
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
        compress_threshold: int = 50 * 1024,
        max_retry: int = 3,
        ttl_days: int = 15,
        redis_client: Optional[Any] = None,
        processing_key: Optional[str] = None,
    ):
        self.r: Any
        if redis_client is not None:
            self.r = redis_client
        elif redis_url:
            self.r = redis.from_url(redis_url, decode_responses=True)
        else:
            raise ValueError("Either redis_url or redis_client must be provided")

        self.namespace = namespace or ""
        self.base = f"{self.namespace}:{queue_name}" if self.namespace else queue_name

        self.queue = self.base
        self.default_processing = f"{self.base}:processing"
        self.processing = processing_key or self.default_processing
        self.retry = f"{self.base}:retry"
        self.dlq = f"{self.base}:dlq"
        self.delay = f"{self.base}:delay"

        self.storage = storage
        self.large_threshold = large_threshold
        self.compress_threshold = compress_threshold
        self.max_retry = max_retry

        self._cctx = zstandard.ZstdCompressor()
        self._dctx = zstandard.ZstdDecompressor()

        self.history = TaskHistory(redis_client=self.r, queue_name=self.base, ttl_days=ttl_days)

    # ==================== Push ====================

    def push(
        self,
        payload: Dict[str, Any],
        delay_seconds: int = 0,
        expire_seconds: int = 0,
    ) -> str:
        """
        推送任务到队列。超过 compress_threshold 的 payload 自动 zstd 压缩，
        优先使用 RemoteStorage（若配置），否则退化为压缩存储。
        """
        try:
            task_id = str(uuid.uuid4())

            original_action = payload.get("action")
            data = orjson.dumps(payload)

            if self.storage and len(data) > self.large_threshold:
                key = self.storage.save_bytes(data)
                payload = {"_large": True, "key": key}
            elif len(data) > self.compress_threshold:
                compressed = self._cctx.compress(data)
                payload = {"_compressed": True, "data": base64.b64encode(compressed).decode("ascii")}

            payload_json = orjson.dumps(payload).decode()
            msg = orjson.dumps({"task_id": task_id, "payload": payload_json}).decode()

            history_data: Dict[str, Any] = {"action": original_action, "status": "pending"}
            if expire_seconds > 0:
                history_data["expires_at"] = time.time() + expire_seconds

            self.history.record(task_id, history_data)

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
            data = orjson.dumps(payload)

            if self.storage and len(data) > self.large_threshold:
                key = self.storage.save_bytes(data)
                payload = {"_large": True, "key": key}
            elif len(data) > self.compress_threshold:
                compressed = self._cctx.compress(data)
                payload = {"_compressed": True, "data": base64.b64encode(compressed).decode("ascii")}

            payload_json = orjson.dumps(payload).decode()
            msg = orjson.dumps({"task_id": task_id, "payload": payload_json}).decode()

            pipe.lpush(self.queue, msg)

            task_key = f"qtask:task:{task_id}"
            hist_data = {
                "action": original_action,
                "status": "pending",
                "task_id": task_id,
                "created_at": time.time(),
            }
            hist_mapping = {
                k: orjson.dumps(v).decode() if isinstance(v, (dict, list)) else v for k, v in hist_data.items()
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

    def _load_large_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("_large") and self.storage:
            raw = self.storage.load(payload["key"])
            return cast(Dict[str, Any], json.loads(raw))
        if payload.get("_compressed"):
            compressed = base64.b64decode(payload["data"])
            raw = self._dctx.decompress(compressed)
            return cast(Dict[str, Any], json.loads(raw))
        return payload

    def _decode_message(self, msg: str) -> Dict[str, Any]:
        data = cast(Dict[str, Any], json.loads(msg))
        if not isinstance(data, dict) or not data.get("task_id"):
            raise ValueError("task_id is required")
        payload_raw = data.get("payload", {})
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        if not isinstance(payload, dict):
            raise ValueError("payload must decode to a JSON object")
        data["payload"] = self._load_large_payload(payload)
        return data

    def _move_poison_to_dlq(self, raw_msg: str, reason: str):
        """将无法解码的 processing 消息移入 DLQ，防止阻塞 recovery。"""
        removed = self._move_from_processing(self.dlq, raw_msg, raw_msg, push_side="left")
        if not removed:
            return

        try:
            data = json.loads(raw_msg)
        except (json.JSONDecodeError, TypeError):
            logger.error(f"无法解码的任务消息移入 DLQ: {reason}")
            return
        if not isinstance(data, dict):
            logger.error(f"非 object 任务消息移入 DLQ: {reason}")
            return

        task_id = data.get("task_id")
        if task_id:
            logger.info(f"任务移入 DLQ: task_id={task_id} reason={reason}")
            self.history.update(task_id, {"status": "failed", "reason": reason})
        else:
            logger.warning(f"无 task_id 的任务消息移入 DLQ: {reason}")

    def _move_from_processing(
        self,
        destination: str,
        raw_msg: str,
        destination_msg: str,
        push_side: str,
    ) -> bool:
        lua_script = """
        local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
        if removed == 0 then
            return 0
        end
        if ARGV[3] == 'left' then
            redis.call('LPUSH', KEYS[2], ARGV[2])
        else
            redis.call('RPUSH', KEYS[2], ARGV[2])
        end
        return removed
        """
        result = self.r.eval(lua_script, 2, self.processing, destination, raw_msg, destination_msg, push_side)
        return bool(result)

    def pop(self, timeout: int = 10) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        从队列获取任务
        """
        try:
            msg = self.r.brpoplpush(self.queue, self.processing, timeout)

            if not msg:
                return None, None

            try:
                data = self._decode_message(msg)
            except Exception as e:
                task_id_hint = ""
                try:
                    raw_data = json.loads(msg)
                    task_id_hint = f" task_id={raw_data.get('task_id', '?')}"
                except Exception:
                    pass
                logger.error(f"Pop decode failed{task_id_hint}: {e}")
                self._move_poison_to_dlq(msg, str(e))
                return None, None

            return data["payload"], msg
        except Exception as e:
            logger.error(f"Pop failed: {e}")
            return None, None

    def pop_no_wait(self) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        """非阻塞 pop"""
        try:
            msg = self.r.rpoplpush(self.queue, self.processing)
            if not msg:
                return None, None

            try:
                data = self._decode_message(msg)
            except Exception as e:
                task_id_hint = ""
                try:
                    raw_data = json.loads(msg)
                    task_id_hint = f" task_id={raw_data.get('task_id', '?')}"
                except Exception:
                    pass
                logger.error(f"Pop no wait decode failed{task_id_hint}: {e}")
                self._move_poison_to_dlq(msg, str(e))
                return None, None

            return data["payload"], msg
        except Exception as e:
            logger.error(f"Pop no wait failed: {e}")
            return None, None

    # ==================== Ack ====================

    def ack(self, raw_msg: str) -> bool:
        """确认任务完成"""
        removed = self.r.lrem(self.processing, 1, raw_msg)
        if not removed:
            logger.warning("Ack ignored because task is not in processing")
            return False

        try:
            data = json.loads(raw_msg)
            task_id = data.get("task_id") if isinstance(data, dict) else None
            if not task_id:
                raise ValueError("task_id is required")
        except Exception as e:
            logger.error(f"Ack history update skipped: {e}")
            return True

        self.history.update(task_id, {"status": "completed"})
        return True

    # ==================== Fail ====================

    def fail(self, raw_msg: str, reason: str = "") -> bool:
        """标记任务失败"""
        try:
            data = json.loads(raw_msg)
            task_id = data.get("task_id") if isinstance(data, dict) else None
            if not task_id:
                raise ValueError("task_id is required")
            payload_raw = data.get("payload", {})
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
            if not isinstance(payload, dict):
                raise ValueError("payload must decode to a JSON object")
        except Exception as e:
            logger.error(f"Fail decode failed: {e}")
            self._move_poison_to_dlq(raw_msg, str(e))
            return False

        retry = payload.get("_retry", 0) + 1
        payload["_retry"] = retry
        new_msg = orjson.dumps({"task_id": task_id, "payload": orjson.dumps(payload).decode()}).decode()

        if retry >= self.max_retry:
            moved = self._move_from_processing(self.dlq, raw_msg, new_msg, push_side="left")
            if not moved:
                logger.warning("Fail ignored because task is not in processing")
                return False
            self.history.update(task_id, {"status": "failed", "reason": reason})
        else:
            moved = self._move_from_processing(self.retry, raw_msg, new_msg, push_side="right")
            if not moved:
                logger.warning("Fail ignored because task is not in processing")
                return False
            self.history.update(task_id, {"status": "retry"})
        return True

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

    def recover_processing_key(self, processing_key: str) -> int:
        """恢复指定 processing 队列中的任务。"""
        count = 0
        while True:
            msg = self.r.rpoplpush(processing_key, self.queue)
            if not msg:
                break
            count += 1
        return count

    def processing_keys(self, include_legacy: bool = True) -> List[str]:
        """返回当前队列的所有 processing keys。"""
        keys = set(self.r.scan_iter(f"{self.base}:processing:*"))
        if include_legacy:
            keys.add(self.default_processing)
        return sorted(keys)

    def recover_stale_processing(self, heartbeat_prefix: str) -> int:
        """恢复没有活跃 heartbeat 的 worker-specific processing 队列。"""
        count = 0
        for key in self.processing_keys(include_legacy=False):
            worker_id = key.rsplit(":", 1)[-1]
            if self.r.exists(f"{heartbeat_prefix}{worker_id}"):
                continue
            count += self.recover_processing_key(key)
        return count

    # ==================== Queue Management ====================

    def size(self) -> int:
        """主队列大小"""
        return int(self.r.llen(self.queue))

    def processing_size(self) -> int:
        """处理中队列大小"""
        return sum(self.r.llen(key) for key in self.processing_keys())

    def retry_size(self) -> int:
        """重试队列大小"""
        return int(self.r.llen(self.retry))

    def dlq_size(self) -> int:
        """死信队列大小"""
        return int(self.r.llen(self.dlq))

    def delay_size(self) -> int:
        """延迟队列大小"""
        return int(self.r.zcard(self.delay))

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
        for key in self.processing_keys():
            self.r.delete(key)
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
