"""任务审计记录。

V2 历史只保存身份、执行元数据与不可变 outcome；live location 以 Redis 容器为准。
live 任务和仍在 DLQ 的失败任务不设置 TTL，避免 operational message 失去审计记录。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, cast

import redis
from loguru import logger

from .clock import Clock, SystemClock

TERMINAL_OUTCOMES = {"completed", "failed", "skipped", "cancelled"}


def _serialize_value(value: Any) -> Any:
    """历史字段序列化：None 转空串，容器转紧凑 JSON，其余原样。"""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


class TaskHistory:
    """按队列维护任务 Hash 与创建时间索引。"""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        queue_name: str = "",
        ttl_days: int = 15,
        redis_client: Optional[Any] = None,
        clock: Clock | None = None,
    ):
        self.r: Any
        if redis_client is not None:
            self.r = redis_client
        elif redis_url:
            self.r = redis.from_url(redis_url, decode_responses=True)
        else:
            raise ValueError("Either redis_url or redis_client must be provided")
        if ttl_days < 0:
            raise ValueError("ttl_days must be >= 0")
        self.queue_name = queue_name
        self.idx_key = f"qtask:hist:{queue_name}"
        self.task_key_prefix = "qtask:task:"
        self.ttl_seconds = ttl_days * 86400
        self.clock = clock or SystemClock()

    def record(self, task_id: str, data: dict[str, Any]) -> None:
        """记录新任务；新记录默认视为仍有 operational message。"""
        pipe = self.r.pipeline()
        self.record_pipeline(pipe, task_id, data)
        pipe.execute()

    def record_pipeline(self, pipe: Any, task_id: str, data: dict[str, Any]) -> None:
        """把任务记录加入调用方 pipeline；live 记录不设置 TTL。"""
        now = self.clock.now()
        record = dict(data)
        record.setdefault("task_id", task_id)
        record.setdefault("_queue", self.queue_name)
        record.setdefault("created_at", now)
        record.setdefault("updated_at", now)
        record.setdefault("outcome", "")
        record.setdefault("status", "pending")
        record.setdefault("operational_message", 1)
        task_key = f"{self.task_key_prefix}{task_id}"
        pipe.hset(task_key, mapping={key: _serialize_value(value) for key, value in record.items()})
        pipe.persist(task_key)
        pipe.zadd(self.idx_key, {task_id: float(record["created_at"])}, nx=True)

    def update(self, task_id: str, fields: dict[str, Any]) -> bool:
        """更新运行元数据；已经写入的 terminal outcome 不允许重新打开或改写。"""
        key = f"{self.task_key_prefix}{task_id}"
        now = self.clock.now()
        mapping = {name: _serialize_value(value) for name, value in fields.items()}
        requested_outcome = str(mapping.get("outcome", ""))
        requested_status = str(mapping.get("status", ""))
        if not requested_outcome and requested_status in TERMINAL_OUTCOMES:
            requested_outcome = requested_status
            mapping["outcome"] = requested_outcome
        mapping["updated_at"] = now
        if requested_outcome in TERMINAL_OUTCOMES:
            mapping.setdefault("status", requested_outcome)
            mapping.setdefault("finished_at", now)

        flat: list[Any] = []
        for field, value in mapping.items():
            flat.extend([field, value])
        # 位置 6 同时被脚本作为 ttl；created score 单独从记录读取，避免更新排序。
        created_at = self.r.hget(key, "created_at") or now
        # 使用独立脚本版本，避免动态 argv 下标容易被调用方字段数影响。
        safe_script = r"""
        if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
        local current = redis.call('HGET', KEYS[1], 'outcome') or ''
        local requested = ARGV[1]
        local requested_status = ARGV[2]
        if current ~= '' and current ~= 'none' then
            if requested ~= '' and requested ~= current then return -1 end
            if requested == '' and requested_status ~= '' and requested_status ~= current then return -1 end
        end
        if #ARGV > 6 then redis.call('HSET', KEYS[1], unpack(ARGV, 7)) end
        redis.call('ZADD', KEYS[2], 'NX', tonumber(ARGV[3]), ARGV[4])
        local outcome = redis.call('HGET', KEYS[1], 'outcome') or ''
        local operational = redis.call('HGET', KEYS[1], 'operational_message') or '0'
        if outcome ~= '' and outcome ~= 'none' and operational == '0' then
            redis.call('EXPIRE', KEYS[1], tonumber(ARGV[5]))
        else
            redis.call('PERSIST', KEYS[1])
        end
        return 1
        """
        result = self.r.eval(
            safe_script,
            2,
            key,
            self.idx_key,
            requested_outcome,
            requested_status,
            created_at,
            task_id,
            max(self.ttl_seconds, 1),
            now,
            *flat,
        )
        return int(result or 0) == 1

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        """获取任务详情，兼容旧版 String 记录。"""
        key = f"{self.task_key_prefix}{task_id}"
        redis_type = self.r.type(key)
        if redis_type == "hash":
            raw_data = self.r.hgetall(key)
            if not raw_data:
                return None
            return {field: self._decode(value) for field, value in raw_data.items()}
        raw_data = self.r.get(key)
        if not raw_data:
            return None
        try:
            parsed = json.loads(raw_data)
            return cast(Dict[str, Any], parsed) if isinstance(parsed, dict) else {"_raw": parsed}
        except (json.JSONDecodeError, TypeError):
            return {"_raw": raw_data}

    def list(self, limit: int = 50) -> List[Dict[str, Any]]:
        """按创建时间倒序列出任务历史。"""
        task_ids = self.r.zrevrange(self.idx_key, 0, max(limit - 1, -1))
        return [record for task_id in task_ids if (record := self.get(str(task_id))) is not None]

    def clear(self) -> None:
        """彻底清空该队列历史；仅供已经显式确认的 destructive 管理操作。"""
        batch_size = 1000
        while True:
            task_ids = self.r.zrange(self.idx_key, 0, batch_size - 1)
            if not task_ids:
                self.r.delete(self.idx_key)
                break
            pipe = self.r.pipeline()
            for task_id in task_ids:
                pipe.delete(f"{self.task_key_prefix}{task_id}")
            pipe.zrem(self.idx_key, *task_ids)
            pipe.execute()

    def clean_expired(self, ttl_seconds: Optional[int] = None) -> int:
        """只清理终态且不再有 operational message 的到期记录。"""
        retention = self.ttl_seconds if ttl_seconds is None else max(int(ttl_seconds), 0)
        cutoff = self.clock.now() - retention
        total_cleaned = 0
        offset = 0
        batch_size = 500

        while True:
            # 索引按 created_at 排序；长跑 live 任务会被安全跳过。
            task_ids = self.r.zrangebyscore(
                self.idx_key,
                "-inf",
                cutoff,
                start=offset,
                num=batch_size,
            )
            if not task_ids:
                break
            removed_this_batch = 0
            for raw_task_id in task_ids:
                task_id = str(raw_task_id)
                record = self.get(task_id)
                if record is None:
                    removed_this_batch += int(self.r.zrem(self.idx_key, task_id) or 0)
                    continue
                outcome = str(record.get("outcome") or record.get("status") or "")
                if outcome not in TERMINAL_OUTCOMES:
                    continue
                if self._task_has_operational_message(task_id):
                    # 修复旧记录缺失/错误的 operational 标记，但不删除。
                    self.r.hset(f"{self.task_key_prefix}{task_id}", "operational_message", "1")
                    continue
                finished_at = self._float_or_none(
                    record.get("finished_at") or record.get("updated_at") or record.get("created_at")
                )
                if finished_at is None or finished_at > cutoff:
                    continue
                removed = self._delete_if_still_archivable(task_id, cutoff)
                removed_this_batch += removed
                total_cleaned += removed

            if removed_this_batch:
                offset = 0
            else:
                offset += len(task_ids)
            if len(task_ids) < batch_size:
                break

        if total_cleaned:
            logger.info(f"[History] Cleaned {total_cleaned} terminal records for {self.queue_name}")
        return total_cleaned

    def _delete_if_still_archivable(self, task_id: str, cutoff: float) -> int:
        """再次在 Redis 内检查终态与 operational 标记，避免 moved=0 后误删。"""
        script = r"""
        if redis.call('EXISTS', KEYS[1]) == 0 then
            return redis.call('ZREM', KEYS[2], ARGV[1])
        end
        local outcome = redis.call('HGET', KEYS[1], 'outcome') or ''
        if outcome == '' then outcome = redis.call('HGET', KEYS[1], 'status') or '' end
        if outcome ~= 'completed' and outcome ~= 'failed' and outcome ~= 'skipped' and outcome ~= 'cancelled' then
            return 0
        end
        local operational = redis.call('HGET', KEYS[1], 'operational_message') or '0'
        if operational ~= '0' then return 0 end
        local finished = tonumber(redis.call('HGET', KEYS[1], 'finished_at') or redis.call('HGET', KEYS[1], 'updated_at') or redis.call('HGET', KEYS[1], 'created_at') or '0')
        if finished > tonumber(ARGV[2]) then return 0 end
        redis.call('DEL', KEYS[1])
        redis.call('ZREM', KEYS[2], ARGV[1])
        return 1
        """
        return int(
            self.r.eval(
                script,
                2,
                f"{self.task_key_prefix}{task_id}",
                self.idx_key,
                task_id,
                cutoff,
            )
            or 0
        )

    def _task_has_operational_message(self, task_id: str) -> bool:
        """兼容旧记录：从容器确认任务是否仍可执行/重放。"""
        list_keys = [
            self.queue_name,
            f"{self.queue_name}:retry",
            f"{self.queue_name}:dlq",
            f"{self.queue_name}:processing",
            *self.r.scan_iter(f"{self.queue_name}:processing:*"),
        ]
        for key in list_keys:
            for raw in self.r.lrange(key, 0, -1):
                if self._message_task_id(raw) == task_id:
                    return True
        for raw, _score in self.r.zscan_iter(f"{self.queue_name}:delay"):
            if self._message_task_id(raw) == task_id:
                return True
        return False

    @staticmethod
    def _message_task_id(raw: Any) -> str | None:
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(value, dict) or not value.get("task_id"):
            return None
        return str(value["task_id"])

    @staticmethod
    def _decode(value: Any) -> Any:
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
