"""独立复核 V2 状态机的关键不变式（使用本机 Redis db15，结束后清理）。"""

import json
import time

import redis

from qtask_list import SmartQueue
from qtask_list.archiver import ArchiveManager

URL = "redis://localhost:6379/15"
r = redis.from_url(URL, decode_responses=True)
r.flushdb()

try:
    print("=== 论断1：归档不会删除仍在 ready 队列的 live 任务历史 ===")
    q = SmartQueue(URL, "t", namespace="rev")
    task_id = q.push({"action": "fetch_kline", "symbol": "AAPL"})
    r.zadd("qtask:hist:rev:t", {task_id: time.time() - 2 * 86400})
    archiver = ArchiveManager(URL, db_dir="/tmp/qtask_review/arch")
    archived = archiver.archive_to_sqlite("rev:t", days_ago=1)
    print(f"归档条数: {archived}")
    print(f"ready 消息还在吗: {r.llen('rev:t') == 1}")
    print(f"Redis 历史还在吗: {r.exists(f'qtask:task:{task_id}') == 1}")

    print()
    print("=== 论断2：max_attempts=3 表示最多执行三次 ===")
    q2 = SmartQueue(URL, "t2", namespace="rev", max_attempts=3, retry_backoff_base=0)
    q2.push({"action": "unstable"})
    attempts = 0
    while q2.dlq_size() == 0:
        _payload, raw = q2.pop_no_wait()
        if raw is None:
            q2.move_delay()
            continue
        attempts += 1
        q2.fail(raw, "err")
    print(f"进入 DLQ 前总执行次数: {attempts}")

    print()
    print("=== 论断3：DLQ 人工重放创建新 task_id，运行元数据不污染 payload ===")
    q3 = SmartQueue(URL, "t3", namespace="rev", max_attempts=2, retry_backoff_base=0)
    old_id = q3.push({"action": "replayable"})
    _payload, raw = q3.pop_no_wait()
    q3.fail(raw, "first")
    q3.move_delay()
    _payload, raw = q3.pop_no_wait()
    q3.fail(raw, "second")
    assert q3.requeue_dlq() == 1
    payload, raw = q3.pop_no_wait()
    new_id = json.loads(raw)["task_id"]
    print(f"旧 task_id: {old_id}")
    print(f"新 task_id: {new_id}，payload 含 _retry 吗: {'_retry' in payload}")
    print(f"replay_of 正确吗: {q3.history.get(new_id)['replay_of'] == old_id}")

    print()
    print("=== 论断4：V2 payload 必须是 JSON object 且 action 独立于业务字段 ===")
    q4 = SmartQueue(URL, "validation", namespace="rev")
    try:
        q4.push([{"action": "a"}, {"action": "b"}])
    except (TypeError, ValueError) as exc:
        print(f"非法 payload 被拒绝: {exc}")
    else:
        print("非法 payload 未被拒绝")
finally:
    r.flushdb()
    print()
    print("清理完成")
