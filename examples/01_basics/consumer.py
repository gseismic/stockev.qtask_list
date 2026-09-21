"""最简用法：不使用 Worker，直接用 SmartQueue 生产 + 消费一个任务。

运行前需要本地 Redis（redis://localhost:6379/0）。先启动消费者（终端 2），
再运行生产者（终端 1）::

    # 终端 1（先启动，阻塞等待任务）
    python examples/01_basics/consumer.py

    # 终端 2
    python examples/01_basics/producer.py

核心要点：
- SmartQueue(namespace, queue_name) 唯一确定一条 Redis 逻辑队列；
- pop() 用 BRPOPLPUSH 可靠消费：任务先进入 processing，ack 后才删除，
  消费者崩溃时任务不丢（recover 可搬回主队列）；
- 这是最底层的 API。生产中优先用 Worker（见 02_worker），它替你处理
  ack/fail/重试/crash recovery。
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import SmartQueue  # noqa: E402

REDIS_URL = "redis://localhost:6379/0"
NAMESPACE = "demo"
QUEUE_NAME = "hello"

queue = SmartQueue(REDIS_URL, QUEUE_NAME, namespace=NAMESPACE)


def main() -> None:
    print(f"等待任务，队列 = {NAMESPACE}:{QUEUE_NAME}（Ctrl+C 退出）")
    while True:
        # 阻塞最多 timeout 秒；返回 (payload, raw_message)，无任务时为 (None, None)。
        # raw_message 是 ack/fail 的凭据，必须妥善传递，不能丢弃。
        payload, raw_msg = queue.pop(timeout=10)
        if raw_msg is None:
            continue  # 超时无任务，继续循环
        print(f"收到任务: {payload}")

        # ... 这里执行业务逻辑 ...

        # ack：任务处理成功，从 processing 删除并记录 completed 历史
        queue.ack(raw_msg)
        print("任务完成 (ack)")
        return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n退出")
