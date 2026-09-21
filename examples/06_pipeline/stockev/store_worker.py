"""Store Worker：消费最终结果并写入幂等存储（示例用 stdout 代替数据库）。

Pipeline 末级：只消费 ``stockev_list:store``，不配置 result_queue，handler 返回 None。
真实实现应把打印换成幂等 upsert（唯一约束 / ON CONFLICT）：at-least-once 语义下，
上游重试或 ack 丢失都会导致同一任务被重复消费。
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import TaskContext, Worker  # noqa: E402

REDIS_URL = "redis://localhost:6379/0"
STOCKEV_NS = "stockev_list"

worker = Worker(
    REDIS_URL,
    "store",
    namespace=STOCKEV_NS,   # stockev_list:store
    # 末级不需要 result_queue：返回 None 即 ack 完成
)


@worker.on("store_result")
def store_result(task: dict, context: TaskContext) -> None:
    symbol = task["symbol"]
    price = task["price"]
    volume = task["volume"]
    ma5 = task["ma5"]
    ma10 = task["ma10"]
    ma20 = task["ma20"]

    # 生产实现应替换为幂等 upsert（如 ON CONFLICT DO UPDATE）：
    # at-least-once 语义下，同一 task 可能因上游重试被投递多次
    print(f"[store] task={context.task_id} {symbol}: ${price} (vol: {volume:,})")
    print(f"        MA5: ${ma5}, MA10: ${ma10}, MA20: ${ma20}")

    # 返回 None 即无 emissions：ack 后本任务进入 completed 终态
    return None


if __name__ == "__main__":
    print(f"Starting store worker for {STOCKEV_NS}:store")
    worker.run()
