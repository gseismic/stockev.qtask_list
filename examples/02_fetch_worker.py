"""
分布式爬虫 - Fetch Worker
消费 fetch 队列，抓取数据后推送到 store 队列
"""
import sys
import os
import time
import random

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qtask_list import Worker, SmartQueue

REDIS_URL = "redis://localhost:6379/0"
NAMESPACE = "stockev_list"

store_q = SmartQueue(REDIS_URL, "store", namespace=NAMESPACE)

worker = Worker(
    REDIS_URL,
    "fetch",
    namespace=NAMESPACE,
    result_queue=store_q,
    max_workers=4,
)


@worker.on("fetch_stock")
def fetch_stock(task):
    symbol = task["symbol"]
    url = task["url"]
    
    print(f"Fetching {symbol} from {url}")
    
    time.sleep(random.uniform(0.1, 0.5))
    
    price = round(random.uniform(50, 500), 2)
    volume = random.randint(1000000, 100000000)
    
    return {
        "action": "store_price",
        "symbol": symbol,
        "price": price,
        "volume": volume,
        "timestamp": time.time(),
    }


if __name__ == "__main__":
    print(f"Starting fetch worker for {NAMESPACE}:fetch")
    worker.run()
