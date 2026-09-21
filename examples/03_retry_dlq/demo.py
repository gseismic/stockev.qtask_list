"""死信队列（DLQ）全流程：投递 → 重试耗尽 → 进 DLQ → 检查 → replay。

一条命令跑完整个生命周期（需要本地 Redis）::

    python examples/03_retry_dlq/demo.py

注意：本脚本假设 demo:dlq-demo 队列为空；若上次运行中断，先清理：
    python -m cli clear demo:dlq-demo --include-history --force

流程说明：
1. 投递一个必然失败的任务（max_attempts=2）
2. 用 SmartQueue 自消费两次，模拟 Worker 耗尽重试 → 任务进入 DLQ
3. 展示 DLQ 中的任务与 QueueAdmin 诊断
4. requeue_dlq 重放：创建**新 task_id**，原终态记录不变（审计友好）
"""

from pathlib import Path
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import QueueAdmin, QueueState, SmartQueue, TaskSpec  # noqa: E402

REDIS_URL = "redis://localhost:6379/0"
NAMESPACE = "demo"
QUEUE_NAME = "dlq-demo"

base = f"{NAMESPACE}:{QUEUE_NAME}"
# retry_backoff_base 调小到 1 秒，让退避等待在演示中可观察（生产默认 30 秒）
queue = SmartQueue(
    REDIS_URL,
    QUEUE_NAME,
    namespace=NAMESPACE,
    max_attempts=2,
    retry_backoff_base=1,
    retry_backoff_max=2,
)
admin = QueueAdmin(REDIS_URL)


def consume_and_fail(reason: str) -> None:
    """消费一个任务并标记失败，模拟 handler 抛错。"""
    payload, raw_msg = queue.pop_no_wait()
    if raw_msg is None:
        print("  (队列空)")
        return
    order_id = (payload or {}).get("order_id", "?")
    print(f"  执行失败: {order_id} ({reason})")
    # fail() 自动判断：attempt < max_attempts → 进 delay 等待重试；否则进 DLQ
    queue.fail(raw_msg, reason, code="simulated_failure")


def main() -> None:
    print("== 1. 投递一个必然失败的任务 ==")
    result = queue.enqueue(
        TaskSpec(
            action="charge_order",
            payload={"order_id": "SO-1001", "amount": 99},
        )
    )
    task_id = result.task_id
    print(f"  task_id = {task_id}")

    print("\n== 2. 消费两次并失败（max_attempts=2）==")
    # 第一次失败：attempt 1 < 2，进入 delay ZSET 等待指数退避（这里只有 1 秒）
    consume_and_fail("第一次: 网关超时")
    # 把重试等待的任务立刻搬回主队列，模拟退避时间已过
    time.sleep(1.2)
    moved = queue.move_delay()
    print(f"  move_delay 搬回 {moved} 个到期任务")
    # 第二次失败：attempt 2 >= 2，进入 DLQ
    consume_and_fail("第二次: 网关仍然超时")

    print("\n== 3. 查看 DLQ 与诊断 ==")
    for task in admin.list_tasks(base, state=QueueState.dlq, limit=10):
        # 列表视图的错误详情在 _raw.last_error；get_task 会展开为可读字段
        last_error = (task.get("_raw") or {}).get("last_error") or {}
        print(f"  DLQ: {task['task_id']} action={task.get('action')} "
              f"last_error={last_error.get('code')}: {last_error.get('reason')}")
    diag = admin.diagnose(base)
    print(f"  诊断: {diag.get('suggestions', [])}")

    print("\n== 4. replay DLQ（创建新 task_id）==")
    # requeue_dlq 不修改原终态记录；修复问题后重放生成全新任务
    requeued = queue.requeue_dlq()
    print(f"  重放 {requeued} 个任务到主队列")
    payload, raw_msg = queue.pop_no_wait()
    if raw_msg:
        print(f"  修复后的任务可正常处理: {payload}")
        queue.ack(raw_msg)
        print("  已 ack，本次演示结束")

    print(f"\n原始失败记录: python -m cli history -t {task_id}")


if __name__ == "__main__":
    main()
