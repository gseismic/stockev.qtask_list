"""Fetch Worker：消费行情任务并以 TaskSpec 发往计算阶段。"""

from datetime import datetime, timezone
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

calculate_q = SmartQueue(REDIS_URL, "calculate", namespace=FINANCE_NS)

worker = Worker(
    REDIS_URL,
    "fetch",
    namespace=STOCKEV_NS,
    result_queue=calculate_q,
    max_workers=4,
)


@worker.on("fetch_stock")
def fetch_stock(task: dict, context: TaskContext) -> TaskResult:
    symbol = task["symbol"]
    url = task["url"]
    
    print(f"[fetch] Fetching {symbol} from {url}")
    
    time.sleep(random.uniform(0.05, 0.2))
    
    price = round(random.uniform(50, 500), 2)
    volume = random.randint(1000000, 100000000)
    
    timestamp = datetime.now(timezone.utc).isoformat()
    return TaskResult(
        value={"symbol": symbol, "price": price, "volume": volume},
        emissions=(
            TaskSpec(
                action="calculate_ma",
                payload={
                    "symbol": symbol,
                    "price": price,
                    "volume": volume,
                    "timestamp": timestamp,
                },
                logical_key=f"calculate:{context.task_id}",
                parent_task_id=context.task_id,
                trace_id=context.trace_id,
            ),
        ),
    )


if __name__ == "__main__":
    print(f"Starting fetch worker for {STOCKEV_NS}:fetch -> {FINANCE_NS}:calculate")
    worker.run()
