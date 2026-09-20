# 独立复核另外两份评审报告的关键新论断（用本机 Redis db15，结束后清理）
import time
import redis
from qtask_list import SmartQueue
from qtask_list.archiver import ArchiveManager

URL = "redis://localhost:6379/15"
r = redis.from_url(URL, decode_responses=True)
r.flushdb()

print("=== 论断1 (GPT-5 R-004): 归档会删掉仍在 ready 队列的 live 任务历史 ===")
q = SmartQueue(URL, "t", namespace="rev")
tid = q.push({"action": "fetch_kline", "symbol": "AAPL"})
# 模拟任务在积压队列里躺了 2 天（历史索引分数改老，等价于 created_at 两天前）
r.zadd("qtask:hist:rev:t", {tid: time.time() - 2 * 86400})
arch = ArchiveManager(URL, db_dir="/tmp/qtask_review/arch")
n = arch.archive_to_sqlite("rev:t", days_ago=1)
print(f"归档条数: {n}")
print(f"ready 消息还在吗: {r.llen('rev:t') == 1}（消息仍在队列）")
print(f"Redis 历史还在吗: {r.exists(f'qtask:task:{tid}') == 1}（已删除 → 论断成立）")
# 后续 ack 会怎样
payload, raw = q.pop_no_wait()
ok = q.ack(raw)
hist = q.history.get(tid)
print(f"ack 返回: {ok}，完成后历史记录: {hist}（生命周期丢失）")

print()
print("=== 论断2 (GPT-5 R-002 备注): max_retry=3 实际是总共执行 3 次 ===")
q2 = SmartQueue(URL, "t2", namespace="rev", max_retry=3)
tid2 = q2.push({"action": "x"})
attempts = 0
while True:
    p, raw = q2.pop_no_wait()
    if raw is None:
        q2.move_retry()
        p, raw = q2.pop_no_wait()
        if raw is None:
            break
    attempts += 1
    q2.fail(raw, "err")
    if q2.dlq_size() > 0:
        break
print(f"进 DLQ 前总执行次数: {attempts}（max_retry=3 → 3 次而非 1+3 次 → 论断成立）")

print()
print("=== 论断3 (GPT-5 R-009): DLQ 重放保留 _retry，再失败一次即回 DLQ ===")
q3 = SmartQueue(URL, "t3", namespace="rev", max_retry=2)
q3.push({"action": "y"})
p, raw = q3.pop_no_wait(); q3.fail(raw, "e")   # 第1次失败 _retry=1
p, raw = q3.pop_no_wait(); q3.fail(raw, "e")   # 第2次失败 _retry=2 >= 2 → DLQ
q3.requeue_dlq()                                # 人工重放
p, raw = q3.pop_no_wait()
print(f"重放后 handler 看到 _retry={p.get('_retry')}（保留原值）")
q3.fail(raw, "e")
print(f"再失败一次后: dlq={q3.dlq_size()}, retry={q3.retry_size()}（单次机会即回 DLQ → 论断成立）")

print()
print("=== 论断4 (GPT-5 R-005): handler 返回 list 推 result_queue 会抛错 ===")
qr = SmartQueue(URL, "down", namespace="rev")
try:
    qr.push([{"action": "a"}, {"action": "b"}])
    print("未抛错 → 论断不成立")
except AttributeError as e:
    print(f"抛错: {e} → 论断成立（在 Worker 中会走 fail → DLQ）")

r.flushdb()
print()
print("清理完成")
