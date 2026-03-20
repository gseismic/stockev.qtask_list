# Classes Reference

## SmartQueue

**Source File:** `qtask_list/queue.py`

Main queue class for all push/pop/ack/fail operations.

### Constructor

```python
SmartQueue(
    redis_url: Optional[str] = None,      # Redis connection URL
    queue_name: str = "",                  # Queue name
    namespace: Optional[str] = None,       # Namespace prefix
    storage: Optional[RemoteStorage] = None,  # Large payload storage
    large_threshold: int = 50 * 1024,     # 50KB threshold for storage
    max_retry: int = 3,                    # Max retry attempts
    ttl_days: int = 15,                    # History TTL in days
    redis_client: Optional[redis.Redis] = None,  # Shared Redis client
)
```

### Key Methods

| Method | Description |
|--------|-------------|
| `push(payload, delay_seconds=0)` | Push task, returns task_id |
| `push_batch(payloads)` | Batch push, returns list of task_ids |
| `pop(timeout=10)` | Blocking pop, returns (payload, raw_msg) |
| `pop_no_wait()` | Non-blocking pop |
| `ack(raw_msg)` | Acknowledge task completion |
| `fail(raw_msg, reason="")` | Mark task failed, triggers retry/DLQ |
| `move_retry()` | Move retry queue tasks to main (FIFO) |
| `move_delay()` | Move expired delay tasks to main |
| `recover()` | Recover processing tasks to main queue |
| `size()` | Main queue length |
| `processing_size()` | Processing queue length |
| `retry_size()` | Retry queue length |
| `dlq_size()` | DLQ length |
| `delay_size()` | Delay queue length |
| `get_stats()` | All queue stats as dict |
| `clear(include_dlq=True)` | Clear all queues |
| `requeue_dlq()` | Re-enqueue DLQ tasks |

### Example

```python
from qtask_list import SmartQueue

q = SmartQueue("redis://localhost:6379/0", "tasks", namespace="myapp")

# Push task
task_id = q.push({"action": "process", "data": "hello"})

# Pop task
payload, raw = q.pop(timeout=5)
if payload:
    # Process...
    q.ack(raw)  # or q.fail(raw, "error reason")
```

---

## Worker

**Source File:** `qtask_list/worker.py`

Event-driven task processor with handler registration.

### Constructor

```python
Worker(
    redis_url: str,                         # Redis connection URL
    queue_name: str,                         # Queue name
    namespace: Optional[str] = None,         # Namespace
    result_queue: Optional[SmartQueue] = None,  # Next stage queue
    storage: Optional[RemoteStorage] = None, # Large payload storage
    max_workers: int = 1,                   # Thread pool size
    max_retry: int = 3,                    # Max retry attempts
    maintenance_interval: int = 1800,       # Maintenance interval (30 min)
    redis_client: Optional[redis.Redis] = None,
)
```

### Key Methods

| Method | Description |
|--------|-------------|
| `on(action)` | Decorator to register handler |
| `run()` | Start worker (blocking) |
| `stop()` | Stop worker gracefully |

### Handler Pattern

```python
from qtask_list import Worker

worker = Worker("redis://localhost:6379/0", "tasks", namespace="myapp")

@worker.on("process")  # Registers handler for action="process"
def handle_process(task):
    # task is the payload dict
    result = do_work(task)
    # Return dict → pushed to result_queue (if configured)
    # Return None → no further action
    return {"action": "next_stage", "data": result}
```

### Concurrency

```python
# Single-threaded
worker = Worker(..., max_workers=1)

# Multi-threaded (4 workers)
worker = Worker(..., max_workers=4)

# Backpressure: max 8 tasks queued when max_workers=4
```

---

## TaskHistory

**Source File:** `qtask_list/history.py`

Task history tracking with Redis Hash storage.

### Constructor

```python
TaskHistory(
    redis_url: Optional[str] = None,
    queue_name: str = "",
    ttl_days: int = 15,
    redis_client: Optional[redis.Redis] = None,
)
```

### Key Methods

| Method | Description |
|--------|-------------|
| `record(task_id, data)` | Record new task |
| `update(task_id, fields)` | Update task fields |
| `get(task_id)` | Get task details |
| `list(limit=50)` | List recent tasks |
| `clear()` | Clear all history |
| `clean_expired(ttl_seconds)` | Clean expired records |

### Usage via SmartQueue

```python
q = SmartQueue(redis_url, "tasks", namespace="myapp")

# Access history
q.history.record(task_id, {"action": "process", "status": "pending"})
q.history.update(task_id, {"status": "completed"})
task = q.history.get(task_id)
```

---

## RemoteStorage

**Source File:** `qtask_list/storage.py`

Remote storage for large payloads (>50KB).

### Constructor

```python
RemoteStorage(
    api_base_url: str,        # Storage API base URL
    timeout: Optional[int] = 30
)
```

### Key Methods

| Method | Description |
|--------|-------------|
| `save_bytes(data)` | Upload bytes, returns key |
| `load(key)` | Download data by key |
| `delete(key)` | Delete data |

### Usage

```python
from qtask_list import SmartQueue, RemoteStorage

storage = RemoteStorage("http://storage-server:8080")

q = SmartQueue(
    "redis://localhost:6379/0",
    "tasks",
    namespace="myapp",
    storage=storage,  # Auto-handles large payloads
    large_threshold=50 * 1024
)
```

---

## ArchiveManager

**Source File:** `qtask_list/archiver.py`

SQLite archiver for historical tasks.

### Constructor

```python
ArchiveManager(
    redis_url: str,
    db_dir: str = "archive_data",
    prefix: str = "qtask_hist"
)
```

### Key Methods

| Method | Description |
|--------|-------------|
| `archive_to_sqlite(queue_full_name, days_ago=1)` | Archive old tasks to SQLite |

---

## Monitor

**Source File:** `qtask_list/archiver.py`

Redis memory health monitor.

### Constructor

```python
Monitor(
    r: redis.Redis,
    threshold_mb: Optional[int] = None
)
```

### Key Methods

| Method | Description |
|--------|-------------|
| `get_memory_info()` | Get memory stats |
| `check_health()` | Check if within threshold |

---

## start_dashboard

**Source File:** `qtask_list/__init__.py`

Launch the web dashboard.

```python
from qtask_list import start_dashboard

start_dashboard(port=8765, redis_url="redis://localhost:6379/0")
```