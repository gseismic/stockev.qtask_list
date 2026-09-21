"""最简用法：投递一个任务。

先启动 consumer.py（终端 1），再运行本脚本（终端 2）。任务会经
BRPOPLPUSH 进入消费者的 processing 队列，被 ack 后进入 completed 历史。
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import SmartQueue, TaskSpec  # noqa: E402

REDIS_URL = "redis://localhost:6379/0"
NAMESPACE = "demo"
QUEUE_NAME = "hello"

queue = SmartQueue(REDIS_URL, QUEUE_NAME, namespace=NAMESPACE)


def main() -> None:
    # V2 推荐：用 TaskSpec 表达一次投递。action 用于路由，payload 只放业务数据。
    result = queue.enqueue(
        TaskSpec(
            action="greet",
            payload={"name": "world", "lang": "zh"},
        )
    )
    print(f"投递结果: {result.as_dict()}")
    print("查看队列状态: python -m qtask_list.cli status demo:hello")


if __name__ == "__main__":
    main()
