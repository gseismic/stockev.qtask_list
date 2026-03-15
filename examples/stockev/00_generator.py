"""
股票数据 Pipeline

流程:
  fetch_stock -> calculate_ma -> store_result

namespace: stockev_list (fetch) -> finance (calculate) -> stockev_list (store)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qtask_list import SmartQueue, Worker

REDIS_URL = "redis://localhost:6379/0"

# 命名空间
STOCKEV_NS = "stockev_list"
FINANCE_NS = "finance"


def main():
    # 创建两个队列
    fetch_q = SmartQueue(REDIS_URL, "fetch", namespace=STOCKEV_NS)
    calculate_q = SmartQueue(REDIS_URL, "calculate", namespace=FINANCE_NS)
    store_q = SmartQueue(REDIS_URL, "store", namespace=STOCKEV_NS)

    symbols = [
        "AAPL", "TSLA", "NVDA", "MSFT", "GOOG",
        "AMZN", "META", "NFLX", "AMD", "INTC",
    ]

    print(f"Generating {len(symbols)} stock tasks...")

    for sym in symbols:
        task_id = fetch_q.push({
            "action": "fetch_stock",
            "symbol": sym,
            "url": f"https://api.example.com/quote/{sym}"
        })
        print(f"  [fetch] {sym} -> task_id: {task_id[:8]}...")

    print("\nPipeline created:")
    print(f"  {STOCKEV_NS}:fetch ({len(symbols)} tasks)")
    print(f"  {FINANCE_NS}:calculate (will be populated by fetch worker)")
    print(f"  {STOCKEV_NS}:store (will be populated by calculate worker)")
    print("\nRun workers in order:")
    print(f"  1. python examples/stockev/calculate_worker.py")
    print(f"  2. python examples/stockev/fetch_worker.py")
    print(f"  3. python examples/stockev/store_worker.py")


if __name__ == "__main__":
    main()
