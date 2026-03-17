import time
import json
import redis
from typing import Optional
from loguru import logger


class TaskHistory:
    """任务历史记录"""

    def __init__(
        self,
        redis_url: Optional[str] = None,
        queue_name: str = "",
        ttl_days: int = 15,
        redis_client: Optional[redis.Redis] = None,
    ):
        if redis_client is not None:
            self.r = redis_client
        elif redis_url:
            self.r = redis.from_url(redis_url, decode_responses=True)
        else:
            raise ValueError("Either redis_url or redis_client must be provided")
        self.queue_name = queue_name
        self.idx_key = f"qtask:hist:{queue_name}"
        self.task_key_prefix = "qtask:task:"
        self.ttl_seconds = ttl_days * 86400

    def record(self, task_id: str, data: dict):
        """记录新任务"""
        data["task_id"] = task_id
        data["created_at"] = time.time()

        task_key = f"{self.task_key_prefix}{task_id}"

        pipe = self.r.pipeline()
        # 使用 hset 存储任务详情，便于原子更新
        # 处理 None 值，Redis 不接受 NoneType
        def serialize_value(v):
            if v is None:
                return ""
            elif isinstance(v, (dict, list)):
                return json.dumps(v)
            return v

        pipe.hset(
            task_key,
            mapping={k: serialize_value(v) for k, v in data.items()},
        )
        pipe.expire(task_key, self.ttl_seconds)

        pipe.zadd(self.idx_key, {task_id: time.time()})
        pipe.expire(self.idx_key, self.ttl_seconds)
        pipe.execute()

    def update(self, task_id: str, fields: dict):
        """更新任务状态 (原子操作)"""
        key = f"{self.task_key_prefix}{task_id}"

        # 处理 None 值
        def serialize_value(v):
            if v is None:
                return ""
            elif isinstance(v, (dict, list)):
                return json.dumps(v)
            return v

        # 使用 hset 直接更新特定字段，保证原子性并减少 RTT
        mapping = {k: serialize_value(v) for k, v in fields.items()}
        mapping["updated_at"] = time.time()

        # 只有当 key 存在时才更新（可选，根据业务逻辑决定）
        # 这里简单处理，直接 hset
        self.r.hset(key, mapping=mapping)

    def get(self, task_id: str) -> dict:
        """获取任务详情 (支持 Hash 和 String 格式)"""
        key = f"{self.task_key_prefix}{task_id}"
        rt = self.r.type(key)

        if rt == "hash":
            raw_data = self.r.hgetall(key)
            if not raw_data:
                return None
            result = {}
            for k, v in raw_data.items():
                try:
                    result[k] = json.loads(v)
                except:
                    result[k] = v
            return result
        else:
            raw_data = self.r.get(key)
            if not raw_data:
                return None
            try:
                return json.loads(raw_data)
            except:
                return {"_raw": raw_data}

    def list(self, limit: int = 50) -> list:
        """列出最近的任务历史 (兼容不同存储格式)"""
        task_ids = self.r.zrevrange(self.idx_key, 0, limit - 1)
        if not task_ids:
            return []

        # 1. 批量获取类型
        pipe = self.r.pipeline()
        for tid in task_ids:
            pipe.type(f"{self.task_key_prefix}{tid}")
        types = pipe.execute()

        # 2. 根据类型批量获取内容
        pipe = self.r.pipeline()
        for tid, rt in zip(task_ids, types):
            if rt == "hash":
                pipe.hgetall(f"{self.task_key_prefix}{tid}")
            else:
                pipe.get(f"{self.task_key_prefix}{tid}")

        raw_results = pipe.execute()
        result = []
        for raw in raw_results:
            if not raw:
                continue

            if isinstance(raw, dict):  # Hash
                item = {}
                for k, v in raw.items():
                    try:
                        item[k] = json.loads(v)
                    except:
                        item[k] = v
                result.append(item)
            else:  # String
                try:
                    result.append(json.loads(raw))
                except:
                    result.append({"_raw": raw})

        return result

    def clear(self):
        """清空历史记录 (分批处理避免阻塞)"""
        batch_size = 1000
        while True:
            task_ids = self.r.zrange(self.idx_key, 0, batch_size - 1)
            if not task_ids:
                break

            pipe = self.r.pipeline()
            for tid in task_ids:
                pipe.delete(f"{self.task_key_prefix}{tid}")
            pipe.zrem(self.idx_key, *task_ids)
            pipe.execute()

    def clean_expired(self, ttl_seconds: int = None) -> int:
        """清理过期历史记录 (分批处理)"""
        if ttl_seconds is None:
            ttl_seconds = self.ttl_seconds

        cutoff = time.time() - ttl_seconds
        total_cleaned = 0
        batch_size = 500

        while True:
            expired_task_ids = self.r.zrangebyscore(
                self.idx_key, "-inf", cutoff, start=0, num=batch_size
            )
            if not expired_task_ids:
                break

            pipe = self.r.pipeline()
            pipe.zrem(self.idx_key, *expired_task_ids)
            for tid in expired_task_ids:
                pipe.delete(f"{self.task_key_prefix}{tid}")
            pipe.execute()

            total_cleaned += len(expired_task_ids)

        if total_cleaned > 0:
            logger.info(f"[History] Cleaned {total_cleaned} expired records for {self.queue_name}")

        return total_cleaned
