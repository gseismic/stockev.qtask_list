# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**qtask_list** is a distributed task queue library based on Redis List, supporting retry, DLQ (Dead Letter Queue), delay tasks, and crash recovery. The project uses Python 3.10+.

## Common Commands

```bash
# Install
pip install -e .

# Install with dev dependencies
pip install -e ".[dev]"

# Install with dashboard
pip install -e ".[dashboard]"

# Run tests
pytest

# Run single test
pytest tests/test_queue.py::test_push_pop

# Lint
ruff check .

# Type check
mypy qtask_list cli
```

## Architecture

### Core Components

1. **SmartQueue** (`qtask_list/queue.py`) - Main queue class
   - Queue keys: `{namespace}:{queue_name}` (main), `{namespace}:{queue_name}:processing`, `{namespace}:{queue_name}:retry`, `{namespace}:{queue_name}:dlq`, `{namespace}:{queue_name}:delay` (sorted set)
   - Methods: `push()`, `push_batch()`, `pop()`, `ack()`, `fail()`, `move_retry()`, `move_delay()`, `recover()`, `requeue_dlq()`

2. **Worker** (`qtask_list/worker.py`) - Event-driven worker
   - Register handlers via `@worker.on("action_name")` decorator
   - Supports multi-threading with `max_workers`
   - Built-in maintenance thread for archiving and memory monitoring
   - Auto crash recovery on startup

3. **TaskHistory** (`qtask_list/history.py`) - Task history tracking
   - Uses Redis sorted set for index and hash for task data
   - TTL-based cleanup (default 15 days)

4. **RemoteStorage** (`qtask_list/storage.py`) - Large payload storage (optional)
   - Stores payloads > 50KB externally

### CLI Commands

```bash
# View queue status
python -m cli status
python -m cli status stockev_list:fetch

# Real-time monitoring
python -m cli watch stockev_list:fetch -i 2

# Clear queue
python -m cli clear stockev_list:fetch --force

# Requeue DLQ tasks
python -m cli requeue stockev_list:fetch --force

# Move retry queue to main
python -m cli retry stockev_list:fetch

# Crash recovery
python -m cli recover stockev_list:fetch

# View history
python -m cli history stockev_list:fetch -l 50
python -m cli history -t <task_id>

# Start worker
python -m cli worker -q fetch -n stockev_list -w 4

# Start dashboard
python -m cli dashboard

# Monitor Redis memory
python -m cli monitor

# Archive history to SQLite
python -m cli archive --days 1

# Clean expired history
python -m cli clean-history stockev_list:fetch -t 15
```

### Queue Status States

| Status | Description |
|--------|-------------|
| Ready | Main queue, pending tasks |
| Processing | Currently being processed (moved back to Ready on crash) |
| Retry | Failed, waiting for retry |
| DLQ | Dead Letter Queue (max retry exceeded) |
| Delay | Delayed tasks (sorted set with timestamp) |

## Worker Startup Order

When using result queues (chained pipelines), start workers in this order:
1. Store worker (consumes final result queue)
2. Calculate worker (if applicable)
3. Fetch worker (produces to next queue)

This prevents tasks from piling up in intermediate queues.

## Environment Variables

```bash
export REDIS_URL=redis://localhost:6379/0
```

## Key Patterns

### Push Task with Result Queue Chain
```python
from qtask_list import Worker, SmartQueue

# Next queue in pipeline
next_q = SmartQueue(redis_url, "calculate", namespace="finance")

worker = Worker(
    redis_url,
    "fetch",
    namespace="stockev_list",
    result_queue=next_q,  # Handler return value goes here
    max_workers=4,
)

@worker.on("fetch_stock")
def handle_fetch(task):
    # Process task...
    return {"action": "calculate_ma", "data": result}

worker.run()
```

### Delay Task
```python
q.push({"action": "fetch_stock", "symbol": "AAPL"}, delay_seconds=60)
```

### CLI Entry Point
The CLI is also available as `qtask` or `qtask_list` commands after installation.

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `redis_url` | `redis://localhost:6379/0` | Redis connection |
| `namespace` | `""` | Queue namespace |
| `max_retry` | `3` | Max retry attempts |
| `large_threshold` | `50KB` | Large payload threshold |

## Queue Key Patterns

| Key | Type | Description |
|-----|------|-------------|
| `{namespace}:{queue_name}` | List | Main queue (ready tasks) |
| `{namespace}:{queue_name}:processing` | List | Currently processing |
| `{namespace}:{queue_name}:retry` | List | Failed, awaiting retry |
| `{namespace}:{queue_name}:dlq` | List | Dead Letter Queue |
| `{namespace}:{queue_name}:delay` | Sorted Set | Delayed tasks (timestamp as score) |
| `qtask:hist:{queue}` | Sorted Set | History index |
| `qtask:task:{task_id}` | Hash | Task data |

## Code Style

### Imports
- Order: stdlib → third-party → local
- Use explicit imports: `from typing import Optional, Dict, Any`
- Never use wildcard imports (`from x import *`)

```python
import json
import time
import redis
from typing import Optional, Any, Dict, List
from loguru import logger

from .history import TaskHistory
from .storage import RemoteStorage
```

### Type Hints
- Use `Optional[X]` instead of `X | None`
- Use explicit generic types: `Dict[str, Any]`, `List[str]`

```python
def push(self, payload: Dict[str, Any], delay_seconds: int = 0) -> str:
def get_stats(self) -> dict:
```

### Naming
- Classes: `PascalCase` (e.g., `SmartQueue`, `Worker`)
- Functions/methods: `snake_case` (e.g., `push`, `pop`)
- Variables: `snake_case` (e.g., `task_id`, `queue_name`)
- Constants: `UPPER_SNAKE_CASE` (e.g., `REDIS_URL`, `MAX_RETRIES`)
- Private methods: `_leading_underscore`

### Error Handling
- Never use bare `except:` - catch specific exceptions
- Use `loguru` logger for logging (imported as `logger`)
- Re-raise exceptions after logging unless handled

```python
try:
    # code
except ValueError as e:
    logger.error(f"Invalid value: {e}")
    raise
except redis.RedisError as e:
    logger.warning(f"Redis error: {e}")
    return None
```

### Redis Patterns
- Use pipeline for batch operations
- Use Lua scripts for atomic operations
- Use `decode_responses=True` for Redis connections

```python
pipe = self.r.pipeline()
pipe.lpush(self.queue, msg)
pipe.hset(task_key, mapping=hist_mapping)
pipe.execute()
```

## Common Pitfalls

1. **Never use undefined variables** - Always check `logger` is imported
2. **Always use timeout for HTTP requests**
3. **Use rpush for FIFO retry queues** (not lpush)
4. **Use atomic Lua scripts for multi-step operations**
5. **Pass redis_client** for shared connections in multi-worker scenarios
6. **Worker startup order matters** - Always start downstream workers first to prevent queue堆积

## Archiver Module

The **ArchiveManager** (`qtask_list/archiver.py`) provides SQLite archiving and Redis memory monitoring:

- **Monitor**: Checks Redis memory usage against configurable threshold
- **ArchiveManager**: Archives expired task history from Redis to SQLite

```python
from qtask_list.archiver import ArchiveManager, Monitor

# Monitor Redis memory
monitor = Monitor(redis_client, threshold_mb=512)
health = monitor.check_health()
memory_info = monitor.get_memory_info()

# Archive to SQLite
archiver = ArchiveManager(redis_url)
count = archiver.archive_to_sqlite("stockev_list:fetch", days_ago=1)
```

## Multi-Namespace Pipeline Example

A 3-stage pipeline across 2 namespaces:

```
stockev_list:fetch → finance:calculate → stockev_list:store
```

**Worker startup order** (critical - always start downstream first):
1. Store worker (`stockev_list:store`)
2. Calculate worker (`finance:calculate`)
3. Fetch worker (`stockev_list:fetch`)

```bash
# Terminal 1: store worker
python examples/stockev/store_worker.py

# Terminal 2: calculate worker
python examples/finance/calculate_worker.py

# Terminal 3: fetch worker
python examples/stockev/fetch_worker.py

# Terminal 4: generate tasks
python examples/generator.py
```

## Dashboard

Start dashboard via CLI or Python API:

```bash
python -m cli dashboard
```

```python
from qtask_list import start_dashboard

start_dashboard(port=8765, redis_url="redis://localhost:6379/0")
```
