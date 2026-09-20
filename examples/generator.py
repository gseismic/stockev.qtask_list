"""股票数据 Pipeline 的 V2 任务生产器。

流程：``fetch_stock -> calculate_ma -> store_result``。身份、窗口和截止时间使用
``TaskSpec`` 表达，重复运行同一交易日不会重复占用队列。
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import SmartQueue, TaskSpec  # noqa: E402

REDIS_URL = "redis://localhost:6379/0"

# 命名空间
STOCKEV_NS = "stockev_list"
FINANCE_NS = "finance"


def main():
    # 创建入口队列
    fetch_q = SmartQueue(REDIS_URL, "fetch", namespace=STOCKEV_NS)

    symbols = [
        "AAPL", "TSLA", "NVDA", "MSFT", "GOOG",
        "AMZN", "META", "NFLX", "AMD", "INTC",
    ]

    slot = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    deadline = slot + timedelta(minutes=7)
    specs = [
        TaskSpec(
            action="fetch_stock",
            payload={
                "symbol": sym,
                "url": f"https://api.example.com/quote/{sym}",
                "partition": f"quotes/trade_date={slot.date().isoformat()}",
            },
            logical_key=f"pipeline:quote:{sym}:{slot.strftime('%Y%m%dT%H%MZ')}",
            scheduled_for=slot,
            start_deadline_at=deadline,
            dedup_until=slot + timedelta(days=2),
        )
        for sym in symbols
    ]
    results = fetch_q.enqueue_many(specs)
    print(f"Generating {len(specs)} stock tasks...")
    for result in results:
        print(f"  [fetch] {result.logical_key} -> {result.reason} ({result.task_id})")

    print("\nPipeline created:")
    print(f"  {STOCKEV_NS}:fetch ({len(symbols)} tasks)")
    print(f"  {FINANCE_NS}:calculate (will be populated by fetch worker)")
    print(f"  {STOCKEV_NS}:store (will be populated by calculate worker)")
    print("\nRun workers in order:")
    print("  1. python examples/stockev/store_worker.py")
    print("  2. python examples/finance/calculate_worker.py")
    print("  3. python examples/stockev/fetch_worker.py")


if __name__ == "__main__":
    main()
