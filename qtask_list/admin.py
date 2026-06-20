import json
import os
import time
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple, cast

import redis

from .queue import SmartQueue


class QueueState(str, Enum):
    """用户可见的任务状态。"""

    ready = "ready"
    processing = "processing"
    retry = "retry"
    dlq = "dlq"
    delay = "delay"
    history = "history"
    all = "all"


class QueueAdmin:
    """面向 Dashboard、CLI 和 Agent 的队列管理接口。"""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        redis_client: Optional[Any] = None,
    ):
        self.redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self.r: Any = redis_client or redis.from_url(self.redis_url, decode_responses=True)

    # ==================== Queue Discovery ====================

    def list_queues(self) -> List[Dict[str, Any]]:
        return [{"name": queue, **self.queue_stats(queue)} for queue in self.queue_names()]

    def queue_names(self) -> List[str]:
        queues = set()
        for key in self.r.scan_iter("qtask:hist:*"):
            queues.add(key.replace("qtask:hist:", ""))

        for key in self.r.scan_iter("*"):
            if self._is_state_key(key) or ":hist:" in key or ":task:" in key:
                continue
            try:
                if self.r.type(key) == "list":
                    queues.add(key)
            except redis.RedisError:
                continue

        for suffix in [":retry", ":dlq", ":delay", ":processing"]:
            for key in self.r.scan_iter(f"*{suffix}"):
                queues.add(key[: -len(suffix)])
        for key in self.r.scan_iter("*:processing:*"):
            queues.add(key.split(":processing:", 1)[0])
        return sorted(queues)

    def queue_stats(self, queue_name: str) -> Dict[str, int]:
        workers = self.list_workers(queue_name)
        return {
            "queue": int(self.r.llen(queue_name)),
            "processing": sum(int(self.r.llen(key)) for key in self.processing_keys(queue_name)),
            "retry": int(self.r.llen(f"{queue_name}:retry")),
            "dlq": int(self.r.llen(f"{queue_name}:dlq")),
            "delay": int(self.r.zcard(f"{queue_name}:delay")),
            "history": int(self.r.zcard(f"qtask:hist:{queue_name}")),
            "active_workers": sum(1 for worker in workers if worker["active"]),
            "stale_workers": sum(1 for worker in workers if not worker["active"]),
        }

    def processing_keys(self, queue_name: str, include_legacy: bool = True) -> List[str]:
        keys = set(self.r.scan_iter(f"{queue_name}:processing:*"))
        if include_legacy:
            keys.add(f"{queue_name}:processing")
        return sorted(keys)

    def list_workers(self, queue_name: Optional[str] = None) -> List[Dict[str, Any]]:
        workers: Dict[Tuple[str, str], Dict[str, Any]] = {}

        for key in self.r.scan_iter("*:worker:*"):
            queue, worker_id = key.split(":worker:", 1)
            if queue_name and queue != queue_name:
                continue
            raw_seen = self.r.get(key)
            last_seen = self._float_or_none(raw_seen)
            workers[(queue, worker_id)] = {
                "queue": queue,
                "worker_id": worker_id,
                "active": True,
                "heartbeat_key": key,
                "ttl": int(self.r.ttl(key)),
                "last_seen": last_seen,
                "processing_key": f"{queue}:processing:{worker_id}",
                "processing": int(self.r.llen(f"{queue}:processing:{worker_id}")),
            }

        for key in self.r.scan_iter("*:processing:*"):
            queue, worker_id = key.split(":processing:", 1)
            if queue_name and queue != queue_name:
                continue
            worker_key = (queue, worker_id)
            if worker_key in workers:
                workers[worker_key]["processing"] = int(self.r.llen(key))
                continue
            workers[worker_key] = {
                "queue": queue,
                "worker_id": worker_id,
                "active": False,
                "heartbeat_key": f"{queue}:worker:{worker_id}",
                "ttl": -2,
                "last_seen": None,
                "processing_key": key,
                "processing": int(self.r.llen(key)),
            }

        return sorted(workers.values(), key=lambda item: (item["queue"], item["worker_id"]))

    # ==================== Task Reading ====================

    def list_tasks(
        self,
        queue_name: str,
        state: QueueState | str = QueueState.all,
        limit: int = 50,
        search: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        selected_state = QueueState(state)
        if selected_state == QueueState.all:
            states = [
                QueueState.ready,
                QueueState.processing,
                QueueState.retry,
                QueueState.dlq,
                QueueState.delay,
            ]
        else:
            states = [selected_state]

        rows: List[Dict[str, Any]] = []
        for item_state in states:
            remaining = max(limit - len(rows), 0)
            if remaining <= 0:
                break
            rows.extend(self._read_state(queue_name, item_state, remaining))

        if search:
            needle = search.lower()
            rows = [
                row
                for row in rows
                if needle in json.dumps(row, ensure_ascii=False, default=str).lower()
            ]
        return rows[:limit]

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        key = f"qtask:task:{task_id}"
        redis_type = self.r.type(key)
        if redis_type == "hash":
            raw = self.r.hgetall(key)
            if not raw:
                return None
            return {field: self._parse_json(value) for field, value in raw.items()}

        raw_value = self.r.get(key)
        if not raw_value:
            return None
        parsed = self._parse_json(raw_value)
        if isinstance(parsed, dict):
            return cast(Dict[str, Any], parsed)
        return {"task_id": task_id, "_raw": raw_value}

    def diagnose(self, queue_name: str) -> Dict[str, Any]:
        stats = self.queue_stats(queue_name)
        workers = self.list_workers(queue_name)
        suggestions = []

        if stats["dlq"] > 0:
            suggestions.append("DLQ 中有失败任务，可先查看详情，再选择单条或批量重放。")
        if stats["retry"] > 0:
            suggestions.append("retry 队列有待重试任务，可手动 drain 到 ready。")
        if stats["stale_workers"] > 0:
            suggestions.append("发现 stale worker processing，可执行安全恢复。")
        if stats["queue"] > 0 and stats["active_workers"] == 0:
            suggestions.append("ready 有积压但没有活跃 Worker，请启动对应消费者。")
        if stats["processing"] > 0 and stats["active_workers"] == 0:
            suggestions.append("processing 有任务但无活跃 Worker，请先执行 stale recover。")
        if not suggestions:
            suggestions.append("未发现需要立即处理的问题。")

        return {
            "queue": queue_name,
            "stats": stats,
            "workers": workers,
            "suggestions": suggestions,
        }

    # ==================== Task Control ====================

    def push_task(
        self,
        queue_name: str,
        payload: Dict[str, Any],
        delay_seconds: int = 0,
    ) -> Dict[str, Any]:
        queue = self._smart_queue(queue_name)
        task_id = queue.push(payload, delay_seconds=delay_seconds)
        return {"queue": queue.base, "task_id": task_id, "delay_seconds": delay_seconds}

    def move_retry(self, queue_name: str) -> Dict[str, int]:
        count = self._drain_list_to_ready(f"{queue_name}:retry", queue_name, update_status=True)
        return {"moved": count}

    def requeue_dlq(
        self,
        queue_name: str,
        task_id: Optional[str] = None,
    ) -> Dict[str, int]:
        if task_id:
            moved = int(self.requeue_task(queue_name, task_id, QueueState.dlq)["moved"])
            return {"moved": moved}
        count = self._drain_list_to_ready(f"{queue_name}:dlq", queue_name, update_status=True)
        return {"moved": count}

    def requeue_task(
        self,
        queue_name: str,
        task_id: str,
        from_state: QueueState | str,
    ) -> Dict[str, Any]:
        state = QueueState(from_state)
        if state in {QueueState.ready, QueueState.history, QueueState.all}:
            return {"moved": 0, "task_id": task_id, "queue": queue_name, "from_state": state.value}

        for key in self._state_keys(queue_name, state):
            if state == QueueState.delay:
                for raw_msg, _score in self.r.zscan_iter(key):
                    if self._message_task_id(raw_msg) == task_id:
                        moved = self._move_delay_message(key, queue_name, raw_msg)
                        self._update_history(task_id, {"status": "pending"})
                        return {
                            "moved": int(moved),
                            "task_id": task_id,
                            "queue": queue_name,
                            "from_state": state.value,
                        }
            else:
                for raw_msg in self.r.lrange(key, 0, -1):
                    if self._message_task_id(raw_msg) == task_id:
                        moved = self._move_list_message(key, queue_name, raw_msg)
                        self._update_history(task_id, {"status": "pending"})
                        return {
                            "moved": int(moved),
                            "task_id": task_id,
                            "queue": queue_name,
                            "from_state": state.value,
                        }

        return {"moved": 0, "task_id": task_id, "queue": queue_name, "from_state": state.value}

    def recover(self, queue_name: str, include_active: bool = False) -> Dict[str, int]:
        recovered = self._drain_list_to_ready(f"{queue_name}:processing", queue_name)
        skipped = 0
        heartbeat_prefix = f"{queue_name}:worker:"

        for key in self.processing_keys(queue_name, include_legacy=False):
            worker_id = key.rsplit(":", 1)[-1]
            if not include_active and self.r.exists(f"{heartbeat_prefix}{worker_id}"):
                skipped += int(self.r.llen(key))
                continue
            recovered += self._drain_list_to_ready(key, queue_name)
        return {"recovered": recovered, "skipped_active": skipped}

    def delete_task(
        self,
        task_id: str,
        queue_name: Optional[str] = None,
    ) -> Dict[str, int]:
        queues = [queue_name] if queue_name else self.queue_names()
        queue_removed = 0
        for queue in queues:
            if not queue:
                continue
            for state in [
                QueueState.ready,
                QueueState.processing,
                QueueState.retry,
                QueueState.dlq,
            ]:
                for key in self._state_keys(queue, state):
                    queue_removed += self._remove_from_list_key(key, task_id)
            queue_removed += self._remove_from_delay_key(f"{queue}:delay", task_id)

        history_records = int(self.r.delete(f"qtask:task:{task_id}") or 0)
        history_indexes = 0
        for hist_key in self.r.scan_iter("qtask:hist:*"):
            history_indexes += int(self.r.zrem(hist_key, task_id) or 0)

        return {
            "queue_messages": queue_removed,
            "history_records": history_records,
            "history_indexes": history_indexes,
        }

    def clear_queue(
        self,
        queue_name: str,
        include_dlq: bool = True,
        include_history: bool = False,
    ) -> Dict[str, int]:
        keys = [queue_name, f"{queue_name}:retry", f"{queue_name}:delay"]
        keys.extend(self.processing_keys(queue_name))
        if include_dlq:
            keys.append(f"{queue_name}:dlq")

        deleted_keys = 0
        for key in keys:
            deleted_keys += int(self.r.delete(key) or 0)

        history_records = 0
        if include_history:
            hist_key = f"qtask:hist:{queue_name}"
            task_ids = self.r.zrange(hist_key, 0, -1)
            history_records = len(task_ids)
            if task_ids:
                pipe = self.r.pipeline()
                for task_id in task_ids:
                    pipe.delete(f"qtask:task:{task_id}")
                pipe.delete(hist_key)
                pipe.execute()
            else:
                self.r.delete(hist_key)

        return {"deleted_keys": deleted_keys, "history_records": history_records}

    # ==================== Internal Helpers ====================

    def _read_state(
        self,
        queue_name: str,
        state: QueueState,
        limit: int,
    ) -> List[Dict[str, Any]]:
        if state == QueueState.history:
            return self._read_history(queue_name, limit)
        if state == QueueState.delay:
            return self._read_delay(queue_name, limit)

        rows: List[Dict[str, Any]] = []
        for key in self._state_keys(queue_name, state):
            remaining = max(limit - len(rows), 0)
            if remaining <= 0:
                break
            messages = list(reversed(self.r.lrange(key, -remaining, -1)))
            for raw_msg in messages:
                rows.append(self._decode_message(raw_msg, queue_name, state.value, key))
        return rows

    def _read_delay(self, queue_name: str, limit: int) -> List[Dict[str, Any]]:
        rows = []
        key = f"{queue_name}:delay"
        for raw_msg, score in self.r.zrange(key, 0, limit - 1, withscores=True):
            item = self._decode_message(raw_msg, queue_name, QueueState.delay.value, key)
            item["run_at"] = score
            item["run_at_text"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(score))
            rows.append(item)
        return rows

    def _read_history(self, queue_name: str, limit: int) -> List[Dict[str, Any]]:
        hist_key = f"qtask:hist:{queue_name}"
        task_ids = self.r.zrevrange(hist_key, 0, limit - 1)
        rows = []
        for task_id in task_ids:
            data = self.get_task(task_id)
            if not data:
                continue
            data["_queue"] = queue_name
            data["_state"] = QueueState.history.value
            data["_source"] = hist_key
            rows.append(data)
        return rows

    def _decode_message(
        self,
        raw_msg: str,
        queue_name: str,
        state: str,
        source_key: str,
    ) -> Dict[str, Any]:
        item: Dict[str, Any] = {
            "task_id": "",
            "action": "",
            "payload": None,
            "_queue": queue_name,
            "_state": state,
            "_source": source_key,
            "_raw": raw_msg,
        }
        try:
            data = json.loads(raw_msg)
        except (json.JSONDecodeError, TypeError) as exc:
            item["decode_error"] = str(exc)
            return item

        if not isinstance(data, dict):
            item["_raw"] = data
            return item

        payload_raw = data.get("payload", {})
        payload = self._parse_json(payload_raw) if isinstance(payload_raw, str) else payload_raw
        item["task_id"] = data.get("task_id", "")
        item["payload"] = payload
        item["_raw"] = data
        if isinstance(payload, dict):
            item["action"] = payload.get("action", "")
            item["retry"] = payload.get("_retry", 0)
            item["status"] = state
        return item

    def _state_keys(self, queue_name: str, state: QueueState) -> List[str]:
        if state == QueueState.ready:
            return [queue_name]
        if state == QueueState.processing:
            return self.processing_keys(queue_name)
        if state == QueueState.retry:
            return [f"{queue_name}:retry"]
        if state == QueueState.dlq:
            return [f"{queue_name}:dlq"]
        if state == QueueState.delay:
            return [f"{queue_name}:delay"]
        return []

    def _drain_list_to_ready(
        self,
        source: str,
        queue_name: str,
        update_status: bool = False,
    ) -> int:
        count = 0
        while True:
            msg = self.r.rpoplpush(source, queue_name)
            if not msg:
                break
            count += 1
            if update_status:
                task_id = self._message_task_id(msg)
                if task_id:
                    self._update_history(task_id, {"status": "pending"})
        return count

    def _move_list_message(self, source: str, destination: str, raw_msg: str) -> bool:
        lua_script = """
        local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
        if removed == 0 then
            return 0
        end
        redis.call('LPUSH', KEYS[2], ARGV[1])
        return removed
        """
        return bool(self.r.eval(lua_script, 2, source, destination, raw_msg))

    def _move_delay_message(self, source: str, destination: str, raw_msg: str) -> bool:
        lua_script = """
        local removed = redis.call('ZREM', KEYS[1], ARGV[1])
        if removed == 0 then
            return 0
        end
        redis.call('LPUSH', KEYS[2], ARGV[1])
        return removed
        """
        return bool(self.r.eval(lua_script, 2, source, destination, raw_msg))

    def _remove_from_list_key(self, key: str, task_id: str) -> int:
        removed = 0
        for raw_msg in self.r.lrange(key, 0, -1):
            if self._message_task_id(raw_msg) == task_id:
                removed += int(self.r.lrem(key, 0, raw_msg) or 0)
        return removed

    def _remove_from_delay_key(self, key: str, task_id: str) -> int:
        removed = 0
        for raw_msg, _score in self.r.zscan_iter(key):
            if self._message_task_id(raw_msg) == task_id:
                removed += int(self.r.zrem(key, raw_msg) or 0)
        return removed

    def _message_task_id(self, raw_msg: str) -> Optional[str]:
        try:
            data = json.loads(raw_msg)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        task_id = data.get("task_id")
        return str(task_id) if task_id else None

    def _smart_queue(self, queue_name: str) -> SmartQueue:
        namespace, short_name = self._split_queue_name(queue_name)
        return SmartQueue(self.redis_url, short_name, namespace=namespace or None, redis_client=self.r)

    def _update_history(self, task_id: str, fields: Dict[str, Any]) -> bool:
        record = self.get_task(task_id)
        if not record:
            return False
        queue_name = record.get("_queue") or self._find_history_queue(task_id)
        if not queue_name:
            return False
        queue = self._smart_queue(str(queue_name))
        return queue.history.update(task_id, fields)

    def _find_history_queue(self, task_id: str) -> Optional[str]:
        for hist_key in self.r.scan_iter("qtask:hist:*"):
            if self.r.zscore(hist_key, task_id) is not None:
                return str(hist_key).replace("qtask:hist:", "")
        return None

    @staticmethod
    def _split_queue_name(queue_name: str) -> Tuple[str, str]:
        if ":" not in queue_name:
            return "", queue_name
        namespace, short_name = queue_name.rsplit(":", 1)
        return namespace, short_name

    @staticmethod
    def _parse_json(value: Any) -> Any:
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value

    @staticmethod
    def _float_or_none(value: Any) -> Optional[float]:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _is_state_key(key: str) -> bool:
        return any(
            marker in key
            for marker in [
                ":processing",
                ":retry",
                ":dlq",
                ":delay",
                ":worker:",
            ]
        )


def task_matches_payload(task: Dict[str, Any], pairs: Iterable[Tuple[str, Any]]) -> bool:
    payload = task.get("payload")
    if not isinstance(payload, dict):
        return False
    return all(payload.get(key) == value for key, value in pairs)
