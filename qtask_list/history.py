import time
import json
import redis
from loguru import logger


class TaskHistory:
    """任务历史记录"""

    def __init__(self, redis_url: str, queue_name: str, ttl_days: int = 15):
        self.r = redis.from_url(redis_url, decode_responses=True)
        self.queue_name = queue_name
        self.idx_key = f"qtask:hist:{queue_name}"
        self.task_key_prefix = "qtask:task:"
        self.ttl_seconds = ttl_days * 86400

    def record(self, task_id: str, data: dict):
        """记录新任务"""
        data["task_id"] = task_id
        data["created_at"] = time.time()

        task_key = f"{self.task_key_prefix}{task_id}"
        self.r.set(task_key, json.dumps(data))
        self.r.expire(task_key, self.ttl_seconds)
        
        self.r.zadd(self.idx_key, {task_id: time.time()})
        self.r.expire(self.idx_key, self.ttl_seconds)

    def update(self, task_id: str, fields: dict):
        """更新任务状态"""
        key = f"{self.task_key_prefix}{task_id}"
        raw = self.r.get(key)
        if not raw:
            return

        data = json.loads(raw)
        data.update(fields)
        data["updated_at"] = time.time()

        self.r.set(key, json.dumps(data))

    def get(self, task_id: str) -> dict:
        """获取任务详情"""
        raw = self.r.get(f"{self.task_key_prefix}{task_id}")
        if raw:
            return json.loads(raw)
        return None

    def list(self, limit: int = 50) -> list:
        """列出最近的任务历史"""
        task_ids = self.r.zrevrange(self.idx_key, 0, limit - 1)

        result = []
        for tid in task_ids:
            raw = self.r.get(f"{self.task_key_prefix}{tid}")
            if raw:
                result.append(json.loads(raw))

        return result

    def clear(self):
        """清空历史记录"""
        task_ids = self.r.zrange(self.idx_key, 0, -1)
        pipe = self.r.pipeline()
        pipe.delete(self.idx_key)
        for tid in task_ids:
            pipe.delete(f"{self.task_key_prefix}{tid}")
        pipe.execute()

    def clean_expired(self, ttl_seconds: int = None) -> int:
        """清理过期历史记录"""
        if ttl_seconds is None:
            ttl_seconds = self.ttl_seconds
        
        cutoff = time.time() - ttl_seconds
        
        expired_task_ids = self.r.zrangebyscore(self.idx_key, '-inf', cutoff)
        
        if not expired_task_ids:
            return 0
        
        pipe = self.r.pipeline()
        pipe.zremrangebyscore(self.idx_key, '-inf', cutoff)
        
        for tid in expired_task_ids:
            pipe.delete(f"{self.task_key_prefix}{tid}")
        
        pipe.execute()
        
        logger.info(f"[History] Cleaned {len(expired_task_ids)} expired records for {self.queue_name}")
        
        return len(expired_task_ids)
