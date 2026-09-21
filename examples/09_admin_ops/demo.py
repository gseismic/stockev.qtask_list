"""QueueAdmin 运维演示：诊断、查任务、重放、恢复。

需要先制造数据（含 DLQ 任务），共三个终端::

    # 终端 1：启动 Worker
    python examples/02_worker/worker.py

    # 终端 2：投递任务（bad_job / no_such_handler 会进 DLQ）
    python examples/02_worker/producer.py

    # 等终端 1 处理完，再运行本脚本
    # 终端 3
    python examples/09_admin_ops/demo.py

QueueAdmin 是 Dashboard / CLI / 运维脚本共用的统一管理 API：
调用方只给 queue_name 字符串，不需要自己拼 Redis key。
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import QueueAdmin, QueueState  # noqa: E402

REDIS_URL = "redis://localhost:6379/0"
QUEUE_NAME = "demo:jobs"

admin = QueueAdmin(REDIS_URL)


def main() -> None:
    print("== 1. 队列发现与状态 ==")
    for info in admin.list_queues():
        if info["name"].startswith("demo:"):
            print(f"  {info['name']}: ready={info['queue']} dlq={info['dlq']} "
                  f"delay={info['delay']} history={info['history']}")

    print("\n== 2. 诊断（stats + 建议）==")
    diag = admin.diagnose(QUEUE_NAME)
    print(f"  stats: {diag.get('stats')}")
    print(f"  建议: {diag.get('suggestions')}")

    print("\n== 3. 按状态浏览任务 ==")
    # completed/failed/skipped/cancelled 按 outcome 过滤历史；dlq 读取队列容器。
    # 注意：DLQ 消息尚未有 outcome（终态记录在 history 中）
    for state in (QueueState.completed, QueueState.failed, QueueState.dlq):
        tasks = admin.list_tasks(QUEUE_NAME, state=state, limit=5)
        print(f"  [{state.value}] {len(tasks)} 条:")
        for task in tasks:
            print(f"    {task['task_id'][:8]}… action={task.get('action')} "
                  f"outcome={task.get('outcome')}")

    print("\n== 4. 单任务详情 ==")
    dlq_tasks = admin.list_tasks(QUEUE_NAME, state=QueueState.dlq, limit=1)
    if dlq_tasks:
        detail = admin.get_task(dlq_tasks[0]["task_id"])
        print(f"  task_id={detail['task_id']}")
        print(f"  last_error={detail.get('last_error') or detail.get('reason_code')}")

        print("\n== 5. 单任务重放（从 DLQ 回到主队列）==")
        # 修复问题后可重放；requeue 创建新 task_id，原终态（failed）记录保留。
        # 重放后的任务停在 ready，等待某个 Worker 处理 —— 本例 handler 仍会
        # 失败并再次进 DLQ，真实场景应先修复 handler 再重放
        ok = admin.requeue_task(QUEUE_NAME, detail["task_id"], from_state=QueueState.dlq)
        print(f"  requeue: {ok}")

    print("\n== 6. 安全恢复（仅失联 Worker 的 processing）==")
    # include_active=False 时只恢复 heartbeat 已过期的 Worker，不会抢活跃任务
    result = admin.recover(QUEUE_NAME)
    print(f"  recover: {result}")

    print("\n提示: CLI 等价命令 -> qtask status / peek / requeue / recover")


if __name__ == "__main__":
    main()
