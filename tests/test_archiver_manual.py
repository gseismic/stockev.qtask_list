import os
import sqlite3
import time
import redis
from qtask_list import SmartQueue
from qtask_list.archiver import ArchiveManager, Monitor

def test_monitor():
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    r = redis.from_url(redis_url, decode_responses=True)
    m = Monitor(r)
    info = m.get_memory_info()
    assert "used_memory_human" in info
    assert "status" in info
    assert m.check_health() is True

def test_archiver():
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    q_name = "test_archive_queue"
    q = SmartQueue(redis_url, q_name)
    
    # 清理旧数据保证环境纯净
    q.history.clear()
    if os.path.exists("./test_archives"):
        import shutil
        shutil.rmtree("./test_archives")

    # 1. 产生一些历史数据
    task_id = q.push({"action": "test_archive", "payload": "hello"})
    q.history.update(task_id, {"status": "completed"})
    
    # 模拟是一天前的
    q.r.zadd(q.history.idx_key, {task_id: time.time() - 86400 * 2})
    
    # 2. 运行归档
    archiver = ArchiveManager(redis_url, db_dir="./test_archives")
    if not os.path.exists("./test_archives"):
        os.makedirs("./test_archives")
        
    count = archiver.archive_to_sqlite(q_name, days_ago=1)
    assert count >= 1
    
    # 3. 验证 SQLite 中有数据
    # 找到今天的 db 文件
    db_file = os.path.join("./test_archives", f"qtask_hist_{time.strftime('%Y%m%d')}.db")
    assert os.path.exists(db_file)
    
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()
    cursor.execute("SELECT task_id, queue_name FROM task_history")
    rows = cursor.fetchall()
    print(f"Rows in DB: {rows}")
    
    cursor.execute("SELECT task_id FROM task_history WHERE queue_name = ?", (q_name,))
    row = cursor.fetchone()
    print(f"Row for {q_name}: {row}")
    assert row is not None
    assert row[0] == task_id
    conn.close()
    
    # 4. 验证 Redis 中已删除
    assert q.r.exists(f"qtask:task:{task_id}") == 0
    assert q.r.zscore(q.history.idx_key, task_id) is None
    
    # 清理
    if os.path.exists(db_file):
        os.remove(db_file)
    if os.path.exists("./test_archives"):
        import shutil
        shutil.rmtree("./test_archives")
    q.history.clear()

if __name__ == "__main__":
    test_monitor()
    test_archiver()
    print("New features tests PASSED")
