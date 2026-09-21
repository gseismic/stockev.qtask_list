"""任务身份（logical_key）与去重：幂等投递的核心机制。

一条命令运行（需要本地 Redis）::

    python examples/05_idempotency/demo.py

核心模型：
- logical_key 是任务的**业务身份**：live 期间（执行中/等待中）同键至多一个任务；
- 任务进入终态后身份仍保留到 dedup_until，期间重复投递得到
  duplicate_retained（指向已完成的旧任务），防止回补重复；
- enqueue/enqueue_many 永不因重复抛异常，而是返回 EnqueueResult：
  reason ∈ enqueued / duplicate_active / duplicate_retained / superseded；
- on_duplicate=ALLOW_NEW 可以显式允许同键新任务（如用户手动重跑）。

注意：身份按「股票 + 日期 + 分钟桶」构造。同一分钟内重复运行会看到
duplicate_* 输出 —— 这正是去重在生效，不是错误；跨分钟运行则得到新身份。
"""

from pathlib import Path
from datetime import datetime, timedelta, timezone
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import DuplicateAction, SmartQueue, TaskSpec  # noqa: E402

REDIS_URL = "redis://localhost:6379/0"
NAMESPACE = "demo"
QUEUE_NAME = "dedup-demo"

queue = SmartQueue(REDIS_URL, QUEUE_NAME, namespace=NAMESPACE)


def main() -> None:
    now = datetime.now(timezone.utc)
    # 业务身份 = 任务类型:主体:分钟桶。确定性字段保证「同一意图」映射到同一
    # 身份；不放入进程启动时间等随机因素，重复运行/补跑天然幂等
    key = f"report:AAPL:{now.strftime('%Y%m%dT%H%M')}"

    def spec(action: str) -> TaskSpec:
        return TaskSpec(
            action=action,
            payload={"symbol": "AAPL"},
            logical_key=key,
            dedup_until=now + timedelta(days=1),
        )

    print("== 1. 同一身份连续投递三次 ==")
    for i in range(3):
        r = queue.enqueue(spec("generate_report"))
        print(f"  第 {i + 1} 次: accepted={r.accepted} reason={r.reason} duplicate_of={r.duplicate_of}")
    print("  -> 只有第一次 enqueued，后两次 duplicate_active（live 期间去重）")

    print("\n== 2. 消费并完成任务后，身份进入保留期 ==")
    payload, raw_msg = queue.pop_no_wait()
    if raw_msg:
        queue.ack(raw_msg)
        print("  任务已完成 (ack)")
    r = queue.enqueue(spec("generate_report"))
    print(f"  再次投递: accepted={r.accepted} reason={r.reason} duplicate_of={r.duplicate_of}")
    print("  -> duplicate_retained：终态身份保留到 dedup_until，防止重复回补")

    print("\n== 3. 显式允许新任务（on_duplicate=ALLOW_NEW）==")
    r = queue.enqueue(spec("generate_report"), on_duplicate=DuplicateAction.ALLOW_NEW)
    print(f"  强制投递: accepted={r.accepted} reason={r.reason} task_id={r.task_id}")
    # 清理：把这个新任务消费掉，避免残留
    payload, raw_msg = queue.pop_no_wait()
    if raw_msg:
        queue.ack(raw_msg)


if __name__ == "__main__":
    main()
