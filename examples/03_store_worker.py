"""
分布式爬虫 - Store Worker
消费 store 队列，将数据存储到数据库
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qtask_list import Worker

REDIS_URL = "redis://localhost:6379/0"
NAMESPACE = "stockev_list"

worker = Worker(
    REDIS_URL,
    "store",
    namespace=NAMESPACE,
)


@worker.on("store_price")
def store_price(task):
    symbol = task["symbol"]
    price = task["price"]
    volume = task["volume"]
    timestamp = task["timestamp"]
    
    print(f"Stored: {symbol} -> ${price} (vol: {volume})")
    
    return None


if __name__ == "__main__":
    print(f"Starting store worker for {NAMESPACE}:store")
    worker.run()
