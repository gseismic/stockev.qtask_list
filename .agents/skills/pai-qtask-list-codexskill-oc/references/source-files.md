# Core Source Files

All core source files are located in `qtask_list/`.

## File Structure

```
qtask_list/
├── __init__.py      # Package exports, start_dashboard()
├── queue.py         # SmartQueue class
├── worker.py        # Worker class
├── history.py       # TaskHistory class
├── storage.py       # RemoteStorage class
└── archiver.py      # ArchiveManager, Monitor classes
```

---

## qtask_list/__init__.py

**Lines:** 42

**Exports:**
- `SmartQueue`
- `Worker`
- `RemoteStorage`
- `start_dashboard`

**Key Function:**

```python
def start_dashboard(port: int = 8765, redis_url: str = "redis://localhost:6379/0"):
    """Launch web dashboard on specified port."""
```

**Full Path:** `/home/lsl/macbook/pai/stockev.qtask_list/qtask_list/__init__.py`

---

## qtask_list/queue.py

**Lines:** 308

**Main Class:** `SmartQueue`

**Queue Keys:**
| Key | Description |
|-----|-------------|
| `{base}` | Main queue (List) |
| `{base}:processing` | Currently processing (List) |
| `{base}:retry` | Retry queue FIFO (List) |
| `{base}:dlq` | Dead Letter Queue (List) |
| `{base}:delay` | Delayed tasks (Sorted Set) |

**Core Methods:**
- `push()` - lpush to main queue
- `push_batch()` - Pipeline batch push
- `pop()` - brpoplpush with timeout
- `pop_no_wait()` - rpoplpush blocking
- `ack()` - lrem from processing
- `fail()` - Increment retry, route to retry or DLQ
- `move_retry()` - FIFO retry to main
- `move_delay()` - Lua atomic delay to main
- `recover()` - processing to main

**Full Path:** `/home/lsl/macbook/pai/stockev.qtask_list/qtask_list/queue.py`

---

## qtask_list/worker.py

**Lines:** 206

**Main Class:** `Worker`

**Key Attributes:**
- `queue: SmartQueue` - The worker's queue
- `result_queue: Optional[SmartQueue]` - Next stage queue
- `handlers: Dict[str, Callable]` - Action handlers
- `max_workers: int` - Thread pool size
- `running: bool` - Shutdown flag

**Handler Registration:**
```python
@worker.on("action_name")
def handler(task: dict) -> Optional[dict]:
    return result  # Pushed to result_queue if set
```

**Lifecycle:**
1. `run()` registers signals
2. `recover()` moves processing → main
3. Starts maintenance thread
4. `_worker_loop()` pops and dispatches
5. `stop()` sets running=False

**Full Path:** `/home/lsl/macbook/pai/stockev.qtask_list/qtask_list/worker.py`

---

## qtask_list/history.py

**Lines:** 184

**Main Class:** `TaskHistory`

**Redis Keys:**
| Key | Description |
|-----|-------------|
| `qtask:hist:{queue_name}` | Sorted set of task IDs by time |
| `qtask:task:{task_id}` | Hash with task details |

**TTL:** `ttl_days * 86400` seconds

**Key Methods:**
- `record()` - Creates task history entry
- `update()` - Updates fields atomically
- `get()` - Retrieves task details
- `list()` - Lists recent tasks with batch fetch
- `clean_expired()` - Removes old entries

**Full Path:** `/home/lsl/macbook/pai/stockev.qtask_list/qtask_list/history.py`

---

## qtask_list/storage.py

**Lines:** 34

**Main Class:** `RemoteStorage`

**Purpose:** Handle payloads > `large_threshold` (default 50KB)

**API Endpoints:**
| Method | Endpoint |
|--------|----------|
| `save_bytes()` | POST `/api/storage/upload` |
| `load()` | GET `/api/storage/download/{key}` |
| `delete()` | DELETE `/api/storage/delete/{key}` |

**Full Path:** `/home/lsl/macbook/pai/stockev.qtask_list/qtask_list/storage.py`

---

## qtask_list/archiver.py

**Lines:** 192

**Classes:**

### Monitor

Memory health checker for Redis.

```python
Monitor(r: redis.Redis, threshold_mb: Optional[int] = None)
```

- `get_memory_info()` - Returns memory stats dict
- `check_health()` - Returns bool, logs warning if exceeded

### ArchiveManager

SQLite archiver for task history.

```python
ArchiveManager(
    redis_url: str,
    db_dir: str = "archive_data",
    prefix: str = "qtask_hist"
)
```

- `archive_to_sqlite()` - Moves expired tasks from Redis to SQLite

**SQLite Schema:**
```sql
CREATE TABLE task_history (
    task_id TEXT PRIMARY KEY,
    queue_name TEXT,
    action TEXT,
    status TEXT,
    payload TEXT,
    result TEXT,
    created_at REAL,
    updated_at REAL,
    raw_data TEXT
)
```

**Full Path:** `/home/lsl/macbook/pai/stockev.qtask_list/qtask_list/archiver.py`

---

## CLI Module

**Path:** `cli/__main__.py`

Commands:
- `status` - Show queue statistics
- `watch` - Real-time monitoring
- `clear` - Clear queues
- `requeue` - Re-enqueue DLQ tasks
- `retry` - Move retry to main
- `recover` - Recover processing tasks
- `worker` - Start worker process
- `dashboard` - Launch web UI
- `monitor` - Redis memory stats
- `archive` - Archive old tasks
- `clean-history` - Clean expired history
- `history` - Query task history

**Full Path:** `/home/lsl/macbook/pai/stockev.qtask_list/cli/__main__.py`

---

## Dashboard

**Path:** `dashboard/main.py`

Web UI for queue monitoring.

**Full Path:** `/home/lsl/macbook/pai/stockev.qtask_list/dashboard/main.py`