import sqlite3
import os
import time
import json
import redis
from loguru import logger
from datetime import datetime
from typing import Optional, List, Dict, Any


class Monitor:
    """Redis 内存监控器"""

    def __init__(self, r: redis.Redis, threshold_mb: Optional[int] = None):
        self.r = r
        self.threshold_mb = threshold_mb

    def get_memory_info(self) -> Dict[str, Any]:
        """获取 Redis 内存统计信息"""
        info = self.r.info("memory")
        return {
            "used_memory_human": info.get("used_memory_human"),
            "used_memory_peak_human": info.get("used_memory_peak_human"),
            "used_memory_lua_human": info.get("used_memory_lua_human"),
            "maxmemory_human": info.get("maxmemory_human"),
            "used_memory": info.get("used_memory"),
            "maxmemory": info.get("maxmemory"),
            "status": "healthy" if self.check_health() else "warning"
        }

    def check_health(self) -> bool:
        """检查内存是否在健康阈值内"""
        if not self.threshold_mb:
            return True
        
        info = self.r.info("memory")
        used = info.get("used_memory", 0)
        
        if used > self.threshold_mb * 1024 * 1024:
            logger.warning(f"Redis memory usage ({info.get('used_memory_human')}) exceeds threshold ({self.threshold_mb} MB)!")
            return False
        return True


class ArchiveManager:
    """SQLite 归档管理器"""

    def __init__(self, redis_url: str, db_dir: str = "archive_data", prefix: str = "qtask_hist"):
        self.redis_url = redis_url
        self.r = redis.from_url(redis_url, decode_responses=True)
        self.db_dir = db_dir
        self.prefix = prefix
        
        if not os.path.exists(self.db_dir):
            os.makedirs(self.db_dir)

    def _get_db_path(self, date_str: str) -> str:
        """获取指定日期的 DB 文件路径"""
        return os.path.join(self.db_dir, f"{self.prefix}_{date_str}.db")

    def _init_db(self, db_path: str):
        """初始化 SQLite 表结构"""
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS task_history (
                    task_id TEXT PRIMARY KEY,
                    queue_name TEXT,
                    action TEXT,
                    status TEXT,
                    payload TEXT,
                    result TEXT,
                    created_at REAL,
                    updated_at REAL,
                    raw_data TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue ON task_history(queue_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON task_history(status)")

    def archive_to_sqlite(self, queue_full_name: str, days_ago: int = 1, batch_size: int = 500) -> int:
        """将过期任务归档到 SQLite"""
        cutoff = time.time() - (days_ago * 86400)
        idx_key = f"qtask:hist:{queue_full_name}"
        task_key_prefix = "qtask:task:"
        
        total_archived = 0
        
        while True:
            # 获取一批过期任务 ID
            task_ids = self.r.zrangebyscore(idx_key, '-inf', cutoff, start=0, num=batch_size)
            if not task_ids:
                break
            
            # 分日期处理，因为可能跨天
            # 为简化，直接按当前归档执行日期分包，或根据任务 created_at 分包
            # 这里选择根据任务 created_at 时间戳决定写入哪个 SQLite 文件
            
            # 修正：支持获取 Hash 或 String 类型的任务数据
            # 1. 第一阶段：批量获取类型
            pipe = self.r.pipeline()
            for tid in task_ids:
                pipe.type(f"{task_key_prefix}{tid}")
            types = pipe.execute()
            
            # 2. 第二阶段：根据类型批量获取内容
            pipe = self.r.pipeline()
            for tid, rtype in zip(task_ids, types):
                key = f"{task_key_prefix}{tid}"
                if rtype == "hash":
                    pipe.hgetall(key)
                elif rtype == "string":
                    pipe.get(key)
                else:
                    pipe.echo("") # 占位符
            
            raw_responses = pipe.execute()
            raw_tasks = []
            
            for j, rtype in enumerate(types):
                data = raw_responses[j]
                if not data:
                    raw_tasks.append(None)
                    continue
                
                if rtype == "hash":
                    # 将 Hash 数据各字段反序列化（如果是 JSON 字符串）
                    decoded = {}
                    for k, v in data.items():
                        try:
                            decoded[k] = json.loads(v)
                        except:
                            decoded[k] = v
                    raw_tasks.append(decoded)
                elif rtype == "string":
                    try:
                        raw_tasks.append(json.loads(data))
                    except:
                        raw_tasks.append({"payload": data})
                else:
                    raw_tasks.append(None)
            
            # 合并写入
            db_sessions = {} # date_str -> List[tuple]
            
            for i, raw in enumerate(raw_tasks):
                if not raw: continue
                tid = task_ids[i]
                
                # 解析字段
                created_at = float(raw.get("created_at", time.time()))
                date_str = datetime.fromtimestamp(created_at).strftime("%Y%m%d")
                
                if date_str not in db_sessions:
                    db_sessions[date_str] = []
                
                db_sessions[date_str].append((
                    tid,
                    queue_full_name,
                    raw.get("action", ""),
                    raw.get("status", ""),
                    raw.get("payload", "{}"),
                    raw.get("result", "{}"),
                    created_at,
                    float(raw.get("updated_at", created_at)),
                    json.dumps(raw)
                ))

            # 执行写入 SQLite 并从 Redis 删除
            for date_str, rows in db_sessions.items():
                db_path = self._get_db_path(date_str)
                self._init_db(db_path)
                
                with sqlite3.connect(db_path) as conn:
                    conn.executemany("""
                        INSERT OR REPLACE INTO task_history 
                        (task_id, queue_name, action, status, payload, result, created_at, updated_at, raw_data)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, rows)
                
                # 写入成功后，从 Redis 删除
                delete_pipe = self.r.pipeline()
                delete_pipe.zrem(idx_key, *[row[0] for row in rows])
                for row in rows:
                    delete_pipe.delete(f"{task_key_prefix}{row[0]}")
                delete_pipe.execute()
                
                total_archived += len(rows)
                
            if len(task_ids) < batch_size:
                break
                
        return total_archived
