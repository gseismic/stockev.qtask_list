import base64
import json
import os
import time
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple, cast

import redis
import zstandard

from .queue import SmartQueue
from .storage import RemoteStorage


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
        storage: Optional[RemoteStorage] = None,
    ):
        self.redis_url = redis_url or os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        self.r: Any = redis_client or redis.from_url(self.redis_url, decode_responses=True)
        self.storage = storage
        self._dctx = zstandard.ZstdDecompressor()

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
        history_counts = self._history_stats(queue_name)
        return {
            "queue": int(self.r.llen(queue_name)),
            "processing": sum(int(self.r.llen(key)) for key in self.processing_keys(queue_name)),
            "retry": int(self.r.llen(f"{queue_name}:retry")),
            "dlq": int(self.r.llen(f"{queue_name}:dlq")),
            "delay": int(self.r.zcard(f"{queue_name}:delay")),
            "history": history_counts["total"],
            "completed": history_counts["completed"],
            "failed": history_counts["failed"],
            "active_workers": sum(1 for worker in workers if worker["active"]),
            "stale_workers": sum(1 for worker in workers if not worker["active"]),
        }

    def _history_stats(self, queue_name: str, sample_limit: int = 2000) -> Dict[str, int]:
        """统计历史任务完成/失败数量。

        在 sample_limit 条内精确计数；超出时按比例外推（近似值）。
        """
        hist_key = f"qtask:hist:{queue_name}"
        total = int(self.r.zcard(hist_key) or 0)
        if total == 0:
            return {"total": 0, "completed": 0, "failed": 0}

        task_ids = self.r.zrevrange(hist_key, 0, sample_limit - 1)
        if not task_ids:
            return {"total": total, "completed": 0, "failed": 0}

        pipe = self.r.pipeline()
        for task_id in task_ids:
            pipe.hget(f"qtask:task:{task_id}", "status")
        statuses = pipe.execute()

        sampled_completed = sum(1 for s in statuses if s == "completed")
        sampled_failed = sum(1 for s in statuses if s == "failed")

        if total <= sample_limit:
            return {"total": total, "completed": sampled_completed, "failed": sampled_failed}

        ratio = total / len(task_ids)
        return {
            "total": total,
            "completed": int(sampled_completed * ratio),
            "failed": int(sampled_failed * ratio),
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

        self._supplement_action_from_history(rows)

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

    def resolve_payload(self, task_id: str, queue_name: str, state: str) -> Dict[str, Any]:
        """从队列中查找任务消息并还原完整 payload（解压 / 拉取外存）。"""
        selected_state = QueueState(state) if state in QueueState._value2member_map_ else QueueState.all

        if selected_state == QueueState.history:
            task = self.get_task(task_id)
            if not task:
                return {"task_id": task_id, "payload": None, "_note": "历史记录不含完整 payload"}
            payload = task.get("payload")
            note = "" if payload else "历史记录不含完整 payload，仅保留 action 和状态"
            result = {"task_id": task_id, "payload": payload, "action": task.get("action", "")}
            if note:
                result["_note"] = note
            return result

        for item_state in ([selected_state] if selected_state != QueueState.all else [
            QueueState.ready, QueueState.processing, QueueState.retry, QueueState.dlq, QueueState.delay,
        ]):
            raw_msg = self._find_raw_message(queue_name, task_id, item_state)
            if raw_msg:
                return self._resolve_payload_from_msg(raw_msg, task_id)

        return {"task_id": task_id, "payload": None, "_note": "未在队列中找到此任务"}

    def _supplement_action_from_history(self, rows: List[Dict[str, Any]]) -> None:
        """对 action 为空且 task_id 非空的行，从 history hash 批量补充 action。"""
        missing = [row for row in rows if not row.get("action") and row.get("task_id")]
        if not missing:
            return

        pipe = self.r.pipeline()
        for row in missing:
            pipe.hget(f"qtask:task:{row['task_id']}", "action")
        actions = pipe.execute()

        for row, action in zip(missing, actions):
            if action:
                row["action"] = action

    def _find_raw_message(self, queue_name: str, task_id: str, state: QueueState) -> Optional[str]:
        for key in self._state_keys(queue_name, state):
            if state == QueueState.delay:
                for raw_msg, _score in self.r.zscan_iter(key):
                    if self._message_task_id(raw_msg) == task_id:
                        return raw_msg
            else:
                for raw_msg in self.r.lrange(key, 0, -1):
                    if self._message_task_id(raw_msg) == task_id:
                        return raw_msg
        return None

    def _resolve_payload_from_msg(self, raw_msg: str, task_id: str) -> Dict[str, Any]:
        try:
            data = json.loads(raw_msg)
        except (json.JSONDecodeError, TypeError):
            return {"task_id": task_id, "payload": None, "_note": "消息解码失败"}

        payload_raw = data.get("payload", {})
        payload = self._parse_json(payload_raw) if isinstance(payload_raw, str) else payload_raw

        if isinstance(payload, dict):
            if payload.get("_compressed"):
                try:
                    compressed = base64.b64decode(payload["data"])
                    raw = self._dctx.decompress(compressed)
                    payload = json.loads(raw)
                except Exception as e:
                    return {"task_id": task_id, "payload": payload, "_note": f"解压失败: {e}"}

            elif payload.get("_large"):
                if self.storage:
                    try:
                        raw = self.storage.load(payload["key"])
                        payload = json.loads(raw)
                    except Exception as e:
                        return {"task_id": task_id, "payload": payload, "_note": f"外存拉取失败: {e}"}
                else:
                    action = ""
                    hist = self.get_task(task_id)
                    if hist:
                        action = hist.get("action", "")
                    return {
                        "task_id": task_id,
                        "payload": payload,
                        "action": action,
                        "_note": "未配置 RemoteStorage，无法还原大 payload",
                    }

        action = payload.get("action", "") if isinstance(payload, dict) else ""
        if not action:
            hist = self.get_task(task_id)
            if hist:
                action = hist.get("action", "")

        return {"task_id": task_id, "payload": payload, "action": action}

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

    def delete_queue(self, queue_name: str) -> Dict[str, int]:
        """彻底删除队列及其所有关联数据（含历史记录），不可撤销。"""
        deleted_keys = 0
        keys_to_delete = [
            queue_name,
            f"{queue_name}:retry",
            f"{queue_name}:dlq",
            f"{queue_name}:delay",
        ]
        keys_to_delete.extend(self.processing_keys(queue_name))
        for key in self.r.scan_iter(f"{queue_name}:worker:*"):
            keys_to_delete.append(key)

        if keys_to_delete:
            deleted_keys += int(self.r.delete(*keys_to_delete) or 0)

        hist_key = f"qtask:hist:{queue_name}"
        history_records = 0
        task_ids = self.r.zrange(hist_key, 0, -1)
        if task_ids:
            pipe = self.r.pipeline()
            for task_id in task_ids:
                pipe.delete(f"qtask:task:{task_id}")
            pipe.delete(hist_key)
            results = pipe.execute()
            history_records = len(task_ids)
            deleted_keys += sum(1 for r in results if r)
        else:
            deleted_keys += int(self.r.delete(hist_key) or 0)

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
        collected_task_ids: List[str] = []
        while True:
            msg = self.r.rpoplpush(source, queue_name)
            if not msg:
                break
            count += 1
            if update_status:
                task_id = self._message_task_id(msg)
                if task_id:
                    collected_task_ids.append(task_id)
        if collected_task_ids:
            self._batch_update_status(collected_task_ids, queue_name)
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

    def _batch_update_status(self, task_ids: List[str], queue_name: str) -> int:
        if not task_ids:
            return 0
        now = time.time()
        queue = self._smart_queue(queue_name)
        ttl_seconds = queue.history.ttl_seconds
        idx_key = queue.history.idx_key
        lua_script = """
        if redis.call('EXISTS', KEYS[1]) == 0 then
            return 0
        end
        redis.call('HSET', KEYS[1], 'status', ARGV[2], 'updated_at', ARGV[1])
        redis.call('EXPIRE', KEYS[1], ARGV[3])
        redis.call('ZADD', KEYS[2], ARGV[1], ARGV[4])
        redis.call('EXPIRE', KEYS[2], ARGV[3])
        return 1
        """
        pipe = self.r.pipeline()
        for task_id in task_ids:
            key = f"qtask:task:{task_id}"
            pipe.eval(lua_script, 2, key, idx_key, now, "pending", ttl_seconds, task_id)
        results = pipe.execute()
        return sum(int(result or 0) for result in results)

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
