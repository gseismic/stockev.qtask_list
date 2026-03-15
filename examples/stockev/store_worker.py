"""
Store Worker - 存储最终结果
消费 stockev_list:store 队列，存储到数据库
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from qtask_list import Worker

REDIS_URL = "redis://localhost:6379/0"
STOCKEV_NS = "stockev_list"

worker = Worker(
    REDIS_URL,
    "store",
    namespace=STOCKEV_NS,
)


@worker.on("store_result")
def store_result(task):
    symbol = task["symbol"]
    price = task["price"]
    volume = task["volume"]
    ma5 = task["ma5"]
    ma10 = task["ma10"]
    ma20 = task["ma20"]
    
    print(f"[store] {symbol}: ${price} (vol: {volume:,})")
    print(f"        MA5: ${ma5}, MA10: ${ma10}, MA20: ${ma20}")
    
    return None


if __name__ == "__main__":
    print(f"Starting store worker for {STOCKEV_NS}:store")
    worker.run()
