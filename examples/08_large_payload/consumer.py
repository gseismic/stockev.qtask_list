"""大 payload 消费者：pop 自动还原完整 payload，业务代码无感知。

运行前先启动外存服务端（见 common.py 模块说明）。
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from common import queue, NAMESPACE, QUEUE_NAME  # noqa: E402


def main() -> None:
    print(f"等待大任务，队列 = {NAMESPACE}:{QUEUE_NAME}")
    payload, raw_msg = queue.pop(timeout=30)
    if raw_msg is None:
        print("超时未取到任务")
        return
    # payload 已被透明还原为完整 dict —— 消费方不感知外存协议
    rows = payload["dataset"]["rows"]
    print(f"还原成功: {len(rows)} 行, 首行 = {rows[0]}")
    queue.ack(raw_msg)
    print("任务完成 (ack)")


if __name__ == "__main__":
    main()
