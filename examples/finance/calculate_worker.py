"""
Calculate Worker - 计算移动平均线
消费 finance:calculate 队列，计算 MA，推送到 stockev_list:store 队列
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

store_q = SmartQueue(REDIS_URL, "store", namespace=STOCKEV_NS)

worker = Worker(
    REDIS_URL,
    "calculate",
    namespace=FINANCE_NS,
    result_queue=store_q,
    max_workers=4,
)


@worker.on("calculate_ma")
def calculate_ma(task):
    symbol = task["symbol"]
    price = task["price"]
    volume = task["volume"]
    
    print(f"[calculate] Computing MA for {symbol} @ ${price}")
    
    time.sleep(random.uniform(0.05, 0.15))
    
    ma5 = round(price * random.uniform(0.98, 1.02), 2)
    ma10 = round(price * random.uniform(0.96, 1.04), 2)
    ma20 = round(price * random.uniform(0.94, 1.06), 2)
    
    return {
        "action": "store_result",
        "symbol": symbol,
        "price": price,
        "volume": volume,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
        "timestamp": task["timestamp"],
    }


if __name__ == "__main__":
    print(f"Starting calculate worker for {FINANCE_NS}:calculate -> {STOCKEV_NS}:store")
    worker.run()
