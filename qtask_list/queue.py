import base64
import json
import random
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple, cast

import orjson
import redis
import zstandard
from loguru import logger

from .history import TaskHistory
from .storage import RemoteStorage

# move_delay 单次 Lua 迁移的批量上限，避免同一秒到期大量任务时阻塞 Redis
MOVE_DELAY_BATCH = 500


class TransientPayloadError(Exception):
    """外存 payload 暂时不可用（如存储服务网络故障），应延后重试而不是进 DLQ。"""


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
        retry_backoff_base: int = 30,
        retry_backoff_max: int = 3600,
        record_history: bool = True,
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
        self.dedup_prefix = f"{self.base}:dedup:"

        self.storage = storage
        self.large_threshold = large_threshold
        self.compress_threshold = compress_threshold
        self.max_retry = max_retry
        # 重试退避：base>0 时 fail 写入 delay ZSET 按指数退避；base=0 保持旧的立即重试行为
        self.retry_backoff_base = retry_backoff_base
        self.retry_backoff_max = retry_backoff_max
        self.record_history = record_history

        # zstandard 上下文不是线程安全的，按线程惰性创建
        self._tls = threading.local()

        self.history = TaskHistory(redis_client=self.r, queue_name=self.base, ttl_days=ttl_days)

    def _compress(self, data: bytes) -> bytes:
        cctx = getattr(self._tls, "cctx", None)
        if cctx is None:
            cctx = zstandard.ZstdCompressor()
            self._tls.cctx = cctx
        return cctx.compress(data)

    def _decompress(self, data: bytes) -> bytes:
        dctx = getattr(self._tls, "dctx", None)
        if dctx is None:
            dctx = zstandard.ZstdDecompressor()
            self._tls.dctx = dctx
        return dctx.decompress(data)

    # ==================== Push ====================

    def _wrap_payload(self, payload: Dict[str, Any]) -> Tuple[Dict[str, Any], Any]:
        """压缩/外存包装。返回 (包装后的 payload, 原始 action)。"""
        original_action = payload.get("action")
        data = orjson.dumps(payload)

        if self.storage and len(data) > self.large_threshold:
            key = self.storage.save_bytes(data)
            return {"_large": True, "key": key}, original_action
        if len(data) > self.compress_threshold:
            compressed = self._compress(data)
            return {"_compressed": True, "data": base64.b64encode(compressed).decode("ascii")}, original_action
        return payload, original_action

    @staticmethod
    def _build_envelope(task_id: str, payload: Dict[str, Any], expires_at: Optional[float] = None) -> str:
        """构建队列消息。expires_at 写入信封，pop 时零额外 RTT 检查执行截止。"""
        envelope: Dict[str, Any] = {"task_id": task_id, "payload": orjson.dumps(payload).decode()}
        if expires_at:
            envelope["expires_at"] = expires_at
        return orjson.dumps(envelope).decode()

    def _default_dedup_ttl(self, expire_seconds: int) -> int:
        return expire_seconds * 2 if expire_seconds > 0 else 86400

    def _try_claim_dedup(self, logical_key: str, task_id: str, ttl: int) -> bool:
        """占用业务身份键。返回 False 表示同键任务已存在（被去重）。"""
        return bool(self.r.set(f"{self.dedup_prefix}{logical_key}", task_id, nx=True, ex=ttl))

    def push(
        self,
        payload: Dict[str, Any],
        delay_seconds: int = 0,
        expire_seconds: int = 0,
        logical_key: Optional[str] = None,
        dedup_ttl: Optional[int] = None,
        force: bool = False,
    ) -> Optional[str]:
        """
        推送任务到队列。超过 compress_threshold 的 payload 自动 zstd 压缩，
        优先使用 RemoteStorage（若配置），否则退化为压缩存储。

        logical_key: 业务身份键（如 "kline:AAPL:1d:2026-09-19"）。同键任务存在时跳过
            投递并返回 None；键值带 TTL 只做垃圾清理，不承担窗口语义（时间桶应编进键本身）。
        force: 显式绕过去重（强制重抓场景）。
        """
        try:
            task_id = str(uuid.uuid4())
            expires_at = time.time() + expire_seconds if expire_seconds > 0 else None

            claimed_key = None
            if logical_key and not force:
                ttl = dedup_ttl or self._default_dedup_ttl(expire_seconds)
                if not self._try_claim_dedup(logical_key, task_id, ttl):
                    return None
                claimed_key = f"{self.dedup_prefix}{logical_key}"

            try:
                wrapped, original_action = self._wrap_payload(payload)
                msg = self._build_envelope(task_id, wrapped, expires_at)

                # 历史记录与入队放同一 pipeline，消除两步之间的非原子窗口
                pipe = self.r.pipeline()
                if self.record_history:
                    history_data: Dict[str, Any] = {"action": original_action, "status": "pending"}
                    if expires_at:
                        history_data["expires_at"] = expires_at
                    if logical_key:
                        history_data["logical_key"] = logical_key
                    self.history.record_pipeline(pipe, task_id, history_data)

                if delay_seconds > 0:
                    pipe.zadd(self.delay, {msg: time.time() + delay_seconds})
                else:
                    pipe.lpush(self.queue, msg)

                pipe.execute()
                return task_id
            except Exception:
                # 投递失败时释放身份键，避免无任务的键占住该 logical_key 直到 TTL
                if claimed_key:
                    try:
                        self.r.delete(claimed_key)
                    except Exception:
                        pass
                raise
        except Exception as e:
            logger.error(f"Push failed: {e}")
            raise

    def push_batch(
        self,
        payloads: List[Dict[str, Any]],
        delay_seconds: int = 0,
        expire_seconds: int = 0,
        logical_keys: Optional[List[Optional[str]]] = None,
        dedup_ttl: Optional[int] = None,
        force: bool = False,
    ) -> List[Optional[str]]:
        """
        批量推送任务。返回与 payloads 等长的 task_id 列表，被去重的项为 None。

        logical_keys: 与 payloads 等长的业务身份键列表，元素可为 None 表示该项不去重。
        """
        if logical_keys is not None and len(logical_keys) != len(payloads):
            raise ValueError("logical_keys length must match payloads")

        task_ids: List[Optional[str]] = [None] * len(payloads)
        ttl = dedup_ttl or self._default_dedup_ttl(expire_seconds)
        claimed: Dict[int, str] = {}
        claimed_keys: List[str] = []

        if logical_keys and not force:
            for i, logical_key in enumerate(logical_keys):
                if not logical_key:
                    continue
                task_id = str(uuid.uuid4())
                if self._try_claim_dedup(logical_key, task_id, ttl):
                    claimed[i] = task_id
                    claimed_keys.append(f"{self.dedup_prefix}{logical_key}")

        expires_at = time.time() + expire_seconds if expire_seconds > 0 else None
        pipe = self.r.pipeline()

        try:
            for i, payload in enumerate(payloads):
                if logical_keys and not force and logical_keys[i] and i not in claimed:
                    continue  # 同键任务已存在，跳过投递

                task_id = claimed.get(i) or str(uuid.uuid4())
                task_ids[i] = task_id

                wrapped, original_action = self._wrap_payload(payload)
                msg = self._build_envelope(task_id, wrapped, expires_at)

                if delay_seconds > 0:
                    pipe.zadd(self.delay, {msg: time.time() + delay_seconds})
                else:
                    pipe.lpush(self.queue, msg)

                if self.record_history:
                    history_data: Dict[str, Any] = {"action": original_action, "status": "pending"}
                    if expires_at:
                        history_data["expires_at"] = expires_at
                    if logical_keys and logical_keys[i]:
                        history_data["logical_key"] = logical_keys[i]
                    self.history.record_pipeline(pipe, task_id, history_data)

            pipe.execute()
        except Exception:
            # 投递失败时释放本次占用的身份键
            if claimed_keys:
                try:
                    self.r.delete(*claimed_keys)
                except Exception:
                    pass
            raise
        return task_ids

    # ==================== Pop ====================

    def _load_large_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if payload.get("_large"):
            if not self.storage:
                raise ValueError(
                    "消息为大 payload（_large），但当前 SmartQueue 未配置 RemoteStorage，无法还原 payload"
                )
            try:
                raw = self.storage.load(payload["key"])
            except Exception as e:
                # 存储服务网络故障是暂时性问题，不应把任务当 poison 丢进 DLQ
                raise TransientPayloadError(f"外存 payload 拉取失败: {e}") from e
            return cast(Dict[str, Any], json.loads(raw))
        if payload.get("_compressed"):
            compressed = base64.b64decode(payload["data"])
            raw = self._decompress(compressed)
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

    def _is_expired(self, data: Dict[str, Any]) -> bool:
        """检查消息信封中的执行截止时间（expires_at）。旧格式消息无此字段则不强制。"""
        expires_at = data.get("expires_at")
        if not expires_at:
            return False
        try:
            return float(expires_at) < time.time()
        except (TypeError, ValueError):
            return False

    def _skip_expired(self, msg: str, task_id: Optional[str]):
        """丢弃已过执行截止的任务：不执行、不进 DLQ（重放也无意义），历史标记 skipped。"""
        removed = self.r.lrem(self.processing, 1, msg)
        if not removed:
            return
        if task_id:
            self.history.update(task_id, {"status": "skipped", "reason": "expired before execution"})

    def _defer_transient(self, msg: str, reason: str):
        """外存暂时不可用：任务移入 delay 队列稍后重试，不进 DLQ。"""
        run_at = time.time() + min(self.retry_backoff_base or 30, 300)
        lua_script = """
        local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
        if removed == 0 then
            return 0
        end
        redis.call('ZADD', KEYS[2], ARGV[2], ARGV[1])
        return 1
        """
        moved = self.r.eval(lua_script, 2, self.processing, self.delay, msg, run_at)
        if not moved:
            return
        task_id = None
        try:
            data = json.loads(msg)
            if isinstance(data, dict):
                task_id = data.get("task_id")
        except (json.JSONDecodeError, TypeError):
            pass
        if task_id:
            self.history.update(str(task_id), {"status": "retry", "reason": reason})

    def _handle_popped(self, msg: str) -> Optional[tuple[Dict[str, Any], str]]:
        """处理一条已进入 processing 的消息。

        返回 (payload, raw_msg) 表示可执行任务；返回 None 表示消息已被
        跳过（过期）、延后（外存暂不可用）或移入 DLQ（poison），调用方应继续取下一条。
        """
        try:
            data = self._decode_message(msg)
        except TransientPayloadError as e:
            logger.warning(f"Payload unavailable, deferred: {e}")
            self._defer_transient(msg, str(e))
            return None
        except Exception as e:
            task_id_hint = ""
            try:
                raw_data = json.loads(msg)
                task_id_hint = f" task_id={raw_data.get('task_id', '?')}"
            except Exception:
                pass
            logger.error(f"Pop decode failed{task_id_hint}: {e}")
            self._move_poison_to_dlq(msg, str(e))
            return None

        if self._is_expired(data):
            logger.info(f"Task expired before execution task_id={data.get('task_id')}, skipped")
            self._skip_expired(msg, data.get("task_id"))
            return None

        return data["payload"], msg

    def pop(self, timeout: int = 10) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        """
        从队列获取任务。已过执行截止（expires_at）的任务在此时被跳过并继续等待下一条。
        """
        while True:
            try:
                msg = self.r.brpoplpush(self.queue, self.processing, timeout)
            except Exception as e:
                logger.error(f"Pop failed: {e}")
                return None, None

            if not msg:
                return None, None

            result = self._handle_popped(msg)
            if result is None:
                continue
            return result

    def pop_no_wait(self) -> tuple[Optional[Dict[str, Any]], Optional[str]]:
        """非阻塞 pop"""
        while True:
            try:
                msg = self.r.rpoplpush(self.queue, self.processing)
            except Exception as e:
                logger.error(f"Pop no wait failed: {e}")
                return None, None

            if not msg:
                return None, None

            result = self._handle_popped(msg)
            if result is None:
                continue
            return result

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
        """标记任务失败。

        retry_backoff_base > 0 时，未耗尽重试次数的任务按指数退避写入 delay 队列
        （delay = base * 2^(retry-1)，±10% 抖动，上限 retry_backoff_max）；
        retry_backoff_base = 0 保持旧的立即重试（retry list + move_retry）行为。
        """
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
        # 信封中的 expires_at 跨重试保留，退避后到期仍未执行则由 pop 跳过
        new_msg = self._build_envelope(str(task_id), payload, data.get("expires_at"))

        if retry >= self.max_retry:
            moved = self._move_from_processing(self.dlq, raw_msg, new_msg, push_side="left")
            if not moved:
                logger.warning("Fail ignored because task is not in processing")
                return False
            self.history.update(str(task_id), {"status": "failed", "reason": reason})
        elif self.retry_backoff_base > 0:
            delay = min(self.retry_backoff_base * (2 ** (retry - 1)), self.retry_backoff_max)
            delay = delay * (0.9 + random.random() * 0.2)
            lua_script = """
            local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
            if removed == 0 then
                return 0
            end
            redis.call('ZADD', KEYS[2], ARGV[2], ARGV[3])
            return 1
            """
            moved = self.r.eval(
                lua_script, 2, self.processing, self.delay, raw_msg, time.time() + delay, new_msg
            )
            if not moved:
                logger.warning("Fail ignored because task is not in processing")
                return False
            self.history.update(str(task_id), {"status": "retry", "reason": reason})
        else:
            moved = self._move_from_processing(self.retry, raw_msg, new_msg, push_side="right")
            if not moved:
                logger.warning("Fail ignored because task is not in processing")
                return False
            self.history.update(str(task_id), {"status": "retry"})
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
        """将延迟队列中已到期的任务移回主队列（原子操作，单次最多迁移 MOVE_DELAY_BATCH 条）"""
        now = time.time()

        lua_script = """
        local count = 0
        while count < tonumber(ARGV[2]) do
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
        result = self.r.eval(lua_script, 2, self.delay, self.queue, now, MOVE_DELAY_BATCH)
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

    def requeue_dlq(self, reset_retry: bool = True) -> int:
        """将 DLQ 中的任务重新入队。

        reset_retry=True（默认）时剥离消息中的 _retry 计数：
        人工重放视为全新尝试，而不是保留已耗尽的重试次数（否则再失败一次就立即回 DLQ）。
        """
        count = 0
        while True:
            msg = self.r.lindex(self.dlq, -1)
            if not msg:
                break
            new_msg = self._reset_retry_message(msg) if reset_retry else msg
            lua_script = """
            local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
            if removed == 0 then
                return 0
            end
            redis.call('LPUSH', KEYS[2], ARGV[2])
            return 1
            """
            moved = self.r.eval(lua_script, 2, self.dlq, self.queue, msg, new_msg)
            if not moved:
                break
            count += 1
        return count

    def _reset_retry_message(self, msg: str) -> str:
        """剥离消息信封 payload 中的 _retry 计数。解码失败时原样返回。"""
        try:
            data = json.loads(msg)
            if not isinstance(data, dict):
                return msg
            payload_raw = data.get("payload", {})
            payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
            if not isinstance(payload, dict):
                return msg
            payload.pop("_retry", None)
            return self._build_envelope(str(data["task_id"]), payload, data.get("expires_at"))
        except Exception:
            return msg
