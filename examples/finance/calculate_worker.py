"""Calculate Worker：计算移动平均线并以 TaskSpec 发往存储阶段。"""

from pathlib import Path
import random
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import SmartQueue, TaskContext, TaskResult, TaskSpec, Worker  # noqa: E402

REDIS_URL = "redis://localhost:6379/0"
STOCKEV_NS = "stockev_list"
FINANCE_NS = "finance"

store_q = SmartQueue(REDIS_URL, "store", namespace=STOCKEV_NS)

worker = Worker(
    REDIS_URL,
    "calculate",
    namespace=FINANCE_NS,
    result_queue=store_q,
    max_workers=4,
)


@worker.on("calculate_ma")
def calculate_ma(task: dict, context: TaskContext) -> TaskResult:
    symbol = task["symbol"]
    price = task["price"]
    volume = task["volume"]
    
    print(f"[calculate] Computing MA for {symbol} @ ${price}")
    
    time.sleep(random.uniform(0.05, 0.15))
    
    ma5 = round(price * random.uniform(0.98, 1.02), 2)
    ma10 = round(price * random.uniform(0.96, 1.04), 2)
    ma20 = round(price * random.uniform(0.94, 1.06), 2)
    
    return TaskResult(
        value={"symbol": symbol, "price": price, "ma5": ma5, "ma10": ma10, "ma20": ma20},
        emissions=(
            TaskSpec(
                action="store_result",
                payload={
                    "symbol": symbol,
                    "price": price,
                    "volume": volume,
                    "ma5": ma5,
                    "ma10": ma10,
                    "ma20": ma20,
                    "timestamp": task["timestamp"],
                },
                logical_key=f"store:{context.task_id}",
                parent_task_id=context.task_id,
                trace_id=context.trace_id,
            ),
        ),
    )


if __name__ == "__main__":
    print(f"Starting calculate worker for {FINANCE_NS}:calculate -> {STOCKEV_NS}:store")
    worker.run()
