"""
Fetch Worker - 爬取股票数据
消费 stockev_list:fetch 队列，推送到 finance:calculate 队列
"""
import sys
import os
import time
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from qtask_list import Worker, SmartQueue

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
def fetch_stock(task):
    symbol = task["symbol"]
    url = task["url"]
    
    print(f"[fetch] Fetching {symbol} from {url}")
    
    time.sleep(random.uniform(0.05, 0.2))
    
    price = round(random.uniform(50, 500), 2)
    volume = random.randint(1000000, 100000000)
    
    return {
        "action": "calculate_ma",
        "symbol": symbol,
        "price": price,
        "volume": volume,
        "timestamp": time.time(),
    }


if __name__ == "__main__":
    print(f"Starting fetch worker for {STOCKEV_NS}:fetch -> {FINANCE_NS}:calculate")
    worker.run()
