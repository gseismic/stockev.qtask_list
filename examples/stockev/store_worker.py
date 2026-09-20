"""Store Worker：消费最终结果并写入幂等存储（示例用 stdout 代替数据库）。"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import TaskContext, Worker  # noqa: E402

REDIS_URL = "redis://localhost:6379/0"
STOCKEV_NS = "stockev_list"

worker = Worker(
    REDIS_URL,
    "store",
    namespace=STOCKEV_NS,
)


@worker.on("store_result")
def store_result(task: dict, context: TaskContext) -> None:
    symbol = task["symbol"]
    price = task["price"]
    volume = task["volume"]
    ma5 = task["ma5"]
    ma10 = task["ma10"]
    ma20 = task["ma20"]
    
    print(f"[store] task={context.task_id} {symbol}: ${price} (vol: {volume:,})")
    print(f"        MA5: ${ma5}, MA10: ${ma10}, MA20: ${ma20}")
    
    return None


if __name__ == "__main__":
    print(f"Starting store worker for {STOCKEV_NS}:store")
    worker.run()
