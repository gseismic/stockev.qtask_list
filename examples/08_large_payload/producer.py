"""大 payload 生产者：投递一个超过阈值的任务。

运行前先启动外存服务端与消费者（见 common.py 模块说明）。
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common import queue, NAMESPACE, QUEUE_NAME  # noqa: E402

from qtask_list import TaskSpec  # noqa: E402


def main() -> None:
    # 约 100KB 的模拟数据集（序列化后原始大小）：超过 10KB 阈值，将自动外存。
    # 即使 zstd 压缩后能放得下，external 判断也优先于压缩 —— 大数据不进 Redis
    big_dataset = {
        "rows": [
            {"id": i, "value": f"row-{i}-" + "x" * 64} for i in range(1000)
        ]
    }
    result = queue.enqueue(
        TaskSpec(
            action="aggregate",
            payload={"dataset": big_dataset, "source": "demo"},
        )
    )
    print(f"投递结果: {result.as_dict()}")
    print("队列引用中只存外存 key（kind=external），Redis 内不含数据本体。")
    print(f"查看: python -m qtask_list.cli peek {NAMESPACE}:{QUEUE_NAME} --state ready --json")


if __name__ == "__main__":
    main()
