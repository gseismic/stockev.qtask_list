"""向 02_worker 的 Worker 投递四种任务：正常、可重试抖动、永久失败、未知 action。

先启动 worker.py（终端 1），再运行本脚本（终端 2）。观察方法（另开终端）::

    python -m qtask_list.cli watch demo:jobs          # 实时看各子队列深度
    python -m qtask_list.cli peek demo:jobs --state dlq   # 看进入死信的任务
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import SmartQueue, TaskSpec  # noqa: E402

REDIS_URL = "redis://localhost:6379/0"
NAMESPACE = "demo"

queue = SmartQueue(REDIS_URL, "jobs", namespace=NAMESPACE)


def main() -> None:
    specs = [
        # 正常完成
        TaskSpec(action="good_job", payload={"n": 1, "msg": "hello"}),
        # 抖动任务：大概率重试 1~2 次后成功
        TaskSpec(action="flaky_job", payload={"n": 2}),
        # 永久失败：立即进 DLQ
        TaskSpec(action="bad_job", payload={"broken": True}),
        # 未知 action：Worker 报 unknown_action 并按永久错误进 DLQ
        TaskSpec(action="no_such_handler", payload={}),
    ]
    for result in queue.enqueue_many(specs):
        # 本示例不设 logical_key，每次运行都是新任务；打印 task_id 便于对照历史
        print(f"{result.logical_key or result.task_id} -> {result.reason}")
    print("\n观察: python -m qtask_list.cli watch demo:jobs")
    print("死信: python -m qtask_list.cli peek demo:jobs --state dlq")


if __name__ == "__main__":
    main()
