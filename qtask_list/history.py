import time
import json
import redis


class TaskHistory:
    """任务历史记录"""

    def __init__(self, redis_url: str, queue_name: str):
        self.r = redis.from_url(redis_url, decode_responses=True)
        self.queue_name = queue_name
        self.idx_key = f"qtask:hist:{queue_name}"
        self.task_key_prefix = "qtask:task:"

    def record(self, task_id: str, data: dict):
        """记录新任务"""
        data["task_id"] = task_id
        data["created_at"] = time.time()

        self.r.set(f"{self.task_key_prefix}{task_id}", json.dumps(data))
        self.r.zadd(self.idx_key, {task_id: time.time()})

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
