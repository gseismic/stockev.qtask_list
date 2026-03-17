# 多阶段流水线完整示例

本示例展示一个股票数据处理的三阶段流水线：
```
stockev_list:fetch → finance:calculate → stockev_list:store
```

## 架构说明

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│    fetch    │───▶│  calculate  │───▶│    store    │
│  (爬取数据)  │    │  (计算指标)  │    │  (存储结果)  │
└─────────────┘    └─────────────┘    └─────────────┘
```

## 1. Store Worker (下游优先启动)

```python
"""
Store Worker - 存储最终结果
消费 stockev_list:store 队列
"""
from qtask_list import Worker

worker = Worker(
    redis_url="redis://localhost:6379/0",
    queue_name="store",
    namespace="stockev_list",
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
    worker.run()
```

## 2. Calculate Worker (中间层)

```python
"""
Calculate Worker - 计算技术指标
消费 finance:calculate 队列，推送到 stockev_list:store
"""
from qtask_list import Worker, SmartQueue

store_q = SmartQueue("redis://localhost:6379/0", "store", namespace="stockev_list")

worker = Worker(
    redis_url="redis://localhost:6379/0",
    queue_name="calculate",
    namespace="finance",
    result_queue=store_q,
)

@worker.on("calculate_ma")
def calculate_ma(task):
    symbol = task["symbol"]
    price = task["price"]
    volume = task["volume"]

    # 模拟计算 MA 指标
    ma5 = round(price * 0.98, 2)
    ma10 = round(price * 0.95, 2)
    ma20 = round(price * 0.90, 2)

    print(f"[calculate] {symbol}: price=${price}, vol={volume:,}")
    print(f"            MA5=${ma5}, MA10=${ma10}, MA20=${ma20}")

    return {
        "action": "store_result",
        "symbol": symbol,
        "price": price,
        "volume": volume,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20,
    }

if __name__ == "__main__":
    worker.run()
```

## 3. Fetch Worker (上游)

```python
"""
Fetch Worker - 爬取股票数据
消费 stockev_list:fetch 队列，推送到 finance:calculate
"""
import time
import random
from qtask_list import Worker, SmartQueue

calculate_q = SmartQueue("redis://localhost:6379/0", "calculate", namespace="finance")

worker = Worker(
    redis_url="redis://localhost:6379/0",
    queue_name="fetch",
    namespace="stockev_list",
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
    worker.run()
```

## 4. 任务生成器

```python
"""
Generator - 产生任务
推送到 stockev_list:fetch 队列
"""
import time
from qtask_list import SmartQueue

fetch_q = SmartQueue("redis://localhost:6379/0", "fetch", namespace="stockev_list")

symbols = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"]

def generate_tasks():
    for symbol in symbols:
        task = {
            "action": "fetch_stock",
            "symbol": symbol,
            "url": f"https://api.example.com/stock/{symbol}",
        }
        task_id = fetch_q.push(task)
        print(f"Pushed: {symbol} (id: {task_id})")
        time.sleep(0.1)

if __name__ == "__main__":
    generate_tasks()
```

## 启动顺序

**重要：始终先启动下游 worker，防止任务堆积**

```bash
# 终端1: 启动 store worker
python examples/stockev/store_worker.py

# 终端2: 启动 calculate worker
python examples/finance/calculate_worker.py

# 终端3: 启动 fetch worker
python examples/stockev/fetch_worker.py

# 终端4: 产生任务
python examples/generator.py
```

## 使用 CLI 监控

```bash
# 查看各队列状态
python -m cli status stockev_list:fetch
python -m cli status finance:calculate
python -m cli status stockev_list:store

# 实时监控
python -m cli watch stockev_list:fetch -i 2
```

## 流水线关键点

1. **启动顺序**：先启动 store → calculate → fetch
2. **result_queue**：每个 worker 的返回值自动推送到下一个队列
3. **action 匹配**：handler 装饰器的参数决定处理哪个 action
4. **返回值**：返回字典会被自动序列化为下一个任务
