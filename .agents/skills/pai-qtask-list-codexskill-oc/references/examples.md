# Examples

## 1. Basic Producer/Consumer

### Producer

```python
"""
Basic task producer
"""
from qtask_list import SmartQueue
import time

q = SmartQueue("redis://localhost:6379/0", "orders", namespace="shop")

for i in range(10):
    task_id = q.push({
        "action": "process_order",
        "order_id": f"ORD-{i:04d}",
        "customer": f"customer_{i}@example.com",
        "items": [{"sku": "ITEM-001", "qty": i + 1}]
    })
    print(f"Queued: ORD-{i:04d} -> {task_id[:8]}")
    time.sleep(0.1)
```

### Consumer Worker

```python
"""
Basic worker with single handler
"""
from qtask_list import Worker

worker = Worker(
    "redis://localhost:6379/0",
    "orders",
    namespace="shop",
    max_workers=2
)

@worker.on("process_order")
def process_order(task):
    order_id = task["order_id"]
    customer = task["customer"]
    items = task["items"]

    print(f"Processing {order_id} for {customer}")
    print(f"  Items: {items}")

    # Simulate work
    return {"status": "processed", "order_id": order_id}

if __name__ == "__main__":
    worker.run()
```

---

## 2. Multi-Stage Pipeline

Three-stage stock data pipeline:
```
fetch → calculate → store
```

### Stage 1: Store Worker (downstream first)

```python
"""
Store Worker - Final result storage
Consumes: stockev_list:store
"""
from qtask_list import Worker

worker = Worker(
    redis_url="redis://localhost:6379/0",
    queue_name="store",
    namespace="stockev_list"
)

@worker.on("store_result")
def store_result(task):
    print(f"[store] {task['symbol']}: ${task['price']}")
    print(f"        MA5=${task['ma5']}, MA10=${task['ma10']}, MA20=${task['ma20']}")
    return None

if __name__ == "__main__":
    worker.run()
```

### Stage 2: Calculate Worker (middle)

```python
"""
Calculate Worker - Technical analysis
Consumes: finance:calculate
Produces: stockev_list:store
"""
from qtask_list import Worker, SmartQueue

store_q = SmartQueue("redis://localhost:6379/0", "store", namespace="stockev_list")

worker = Worker(
    redis_url="redis://localhost:6379/0",
    queue_name="calculate",
    namespace="finance",
    result_queue=store_q
)

@worker.on("calculate_ma")
def calculate_ma(task):
    symbol = task["symbol"]
    price = task["price"]

    # Compute moving averages
    ma5 = round(price * 0.98, 2)
    ma10 = round(price * 0.95, 2)
    ma20 = round(price * 0.90, 2)

    print(f"[calculate] {symbol}: ${price} -> MA5=${ma5}, MA10=${ma10}, MA20=${ma20}")

    # Return dict → pushed to result_queue (store_q)
    return {
        "action": "store_result",
        "symbol": symbol,
        "price": price,
        "ma5": ma5,
        "ma10": ma10,
        "ma20": ma20
    }

if __name__ == "__main__":
    worker.run()
```

### Stage 3: Fetch Worker (upstream)

```python
"""
Fetch Worker - Data fetching
Consumes: stockev_list:fetch
Produces: finance:calculate
"""
from qtask_list import Worker, SmartQueue
import random

calculate_q = SmartQueue("redis://localhost:6379/0", "calculate", namespace="finance")

worker = Worker(
    redis_url="redis://localhost:6379/0",
    queue_name="fetch",
    namespace="stockev_list",
    result_queue=calculate_q,
    max_workers=4
)

@worker.on("fetch_stock")
def fetch_stock(task):
    symbol = task["symbol"]
    url = task["url"]

    print(f"[fetch] Getting {symbol} from {url}")

    # Simulate API call
    price = round(random.uniform(50, 500), 2)

    # Return → pushed to result_queue (calculate_q)
    return {
        "action": "calculate_ma",
        "symbol": symbol,
        "price": price
    }

if __name__ == "__main__":
    worker.run()
```

### Task Generator

```python
"""
Generate fetch tasks
"""
from qtask_list import SmartQueue

fetch_q = SmartQueue("redis://localhost:6379/0", "fetch", namespace="stockev_list")

symbols = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA"]

for sym in symbols:
    task_id = fetch_q.push({
        "action": "fetch_stock",
        "symbol": sym,
        "url": f"https://api.example.com/quote/{sym}"
    })
    print(f"Pushed: {sym} -> {task_id[:8]}...")
```

### Startup Order

```bash
# 1. Start downstream first
python store_worker.py

# 2. Start middle worker
python calculate_worker.py

# 3. Start upstream worker
python fetch_worker.py

# 4. Generate tasks
python generator.py
```

---

## 3. Delayed Tasks

```python
"""
Delayed task example - Send email after 60 seconds
"""
from qtask_list import SmartQueue
import time

q = SmartQueue("redis://localhost:6379/0", "emails", namespace="myapp")

# Schedule email in 60 seconds
task_id = q.push({
    "action": "send_email",
    "to": "user@example.com",
    "subject": "Hello",
    "body": "Delayed message"
}, delay_seconds=60)

print(f"Email scheduled: {task_id}")
print(f"Will be delivered in 60 seconds...")

# The move_delay() is called by Worker automatically
# For manual testing:
# q.move_delay()
```

---

## 4. Retry and DLQ Pattern

```python
"""
Demonstrating retry/DLQ behavior
max_retry=2 means: attempt 1, attempt 2, then DLQ
"""
from qtask_list import SmartQueue

q = SmartQueue(
    "redis://localhost:6379/0",
    " unreliable_tasks",
    namespace="myapp",
    max_retry=2
)

# Push a task
task_id = q.push({"action": "unreliable", "data": "test"})

# Pop and fail (retry=1)
payload, raw = q.pop()
q.fail(raw, "Connection timeout")

# Pop and fail again (retry=2)
payload, raw = q.pop()
q.fail(raw, "Connection timeout")

# Pop - now goes to DLQ (retry=3 > max_retry=2)
# DLQ = {namespace}:{queue}:dlq
print(f"DLQ size: {q.dlq_size()}")  # 1
```

Requeue from DLQ:

```python
# Requeue all DLQ tasks
count = q.requeue_dlq()
print(f"Requeued {count} tasks from DLQ")
```

---

## 5. Batch Operations

```python
"""
Batch push for high throughput
"""
from qtask_list import SmartQueue

q = SmartQueue("redis://localhost:6379/0", "batch", namespace="myapp")

# Batch push - atomic with pipeline
task_ids = q.push_batch([
    {"action": "process", "id": i, "data": f"item-{i}"}
    for i in range(100)
])

print(f"Pushed {len(task_ids)} tasks atomically")
print(f"Queue size: {q.size()}")  # 100
```

---

## 6. Worker with result_queue Chaining

```python
"""
Worker that chains to multiple queues
"""
from qtask_list import Worker, SmartQueue

# Multiple output queues
notify_q = SmartQueue("redis://localhost:6379/0", "notifications", namespace="myapp")
audit_q = SmartQueue("redis://localhost:6379/0", "audit", namespace="myapp")

class ChainedWorker(Worker):
    def _process_task(self, payload, raw_msg):
        action = payload.get("action")
        handler = self.handlers.get(action)

        if not handler:
            self.queue.fail(raw_msg, f"unknown action: {action}")
            return False

        try:
            result = handler(payload)

            # Chain to multiple queues
            if result:
                if result.get("notify"):
                    notify_q.push(result["notify"])
                if result.get("audit"):
                    audit_q.push(result["audit"])

            self.queue.ack(raw_msg)
            return True
        except Exception as e:
            self.queue.fail(raw_msg, str(e))
            return False

worker = ChainedWorker(
    "redis://localhost:6379/0",
    "orders",
    namespace="myapp"
)
```

---

## 7. Large Payload Handling

```python
"""
Using RemoteStorage for large payloads
"""
from qtask_list import SmartQueue, RemoteStorage

storage = RemoteStorage("http://storage-server:8080")

q = SmartQueue(
    "redis://localhost:6379/0",
    "large_tasks",
    namespace="myapp",
    storage=storage,
    large_threshold=50 * 1024  # 50KB threshold
)

# Automatically stored remotely if > 50KB
task_id = q.push({
    "action": "process_file",
    "filename": "huge_dataset.csv",
    "data": open("huge_dataset.csv", "rb").read()  # Large data
})

# Payload retrieved automatically on pop()
payload, raw = q.pop()
```

---

## 8. Crash Recovery

```python
"""
Simulating crash recovery
"""
from qtask_list import SmartQueue

q = SmartQueue("redis://localhost:6379/0", "tasks", namespace="myapp")

# Push some tasks
for i in range(5):
    q.push({"action": "process", "id": i})

# Pop but don't ack/fail (simulates crash)
for i in range(3):
    q.pop()

print(f"Processing queue: {q.processing_size()}")  # 3

# On worker startup, recover() is called automatically
# Manual recovery:
count = q.recover()
print(f"Recovered {count} tasks")
print(f"Processing queue: {q.processing_size()}")  # 0
print(f"Main queue: {q.size()}")  # 5
```

---

## 9. Task History

```python
"""
Querying task history
"""
from qtask_list import SmartQueue

q = SmartQueue("redis://localhost:6379/0", "tasks", namespace="myapp", ttl_days=7)

# Push and process some tasks
for i in range(10):
    q.push({"action": "process", "id": i})

# Get recent tasks
history = q.history.list(limit=5)
for task in history:
    print(f"{task['task_id'][:8]} - {task['action']} - {task['status']}")

# Get specific task
task_id = history[0]["task_id"]
details = q.history.get(task_id)
print(f"Details: {details}")

# Clean expired
cleaned = q.history.clean_expired(ttl_seconds=0)  # Clean all for testing
print(f"Cleaned {cleaned} expired records")
```

---

## 10. Statistics and Monitoring

```python
"""
Queue statistics
"""
from qtask_list import SmartQueue

q = SmartQueue("redis://localhost:6379/0", "tasks", namespace="myapp")

# Get all stats
stats = q.get_stats()
print(f"""
Queue:        {stats['queue']}
Processing:   {stats['processing']}
Retry:        {stats['retry']}
DLQ:          {stats['dlq']}
Delay:        {stats['delay']}
""")

# Individual sizes
print(f"Main queue: {q.size()}")
print(f"DLQ: {q.dlq_size()}")
```

---

## 11. CLI Worker Quick Start

```bash
# Using CLI to run workers without Python code

# Start worker with 4 threads
python -m cli worker -q fetch -n stockev_list -w 4 -r redis://localhost:6379/0

# The CLI worker requires handlers defined via --handler option or reads from module
# For complex handlers, use Python worker code instead
```