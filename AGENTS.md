# AGENTS.md - Agent Coding Guidelines

This file provides guidance for AI agents working in this repository.

## Project Overview

**qtask_list** is a distributed task queue library based on Redis List, supporting retry, DLQ (Dead Letter Queue), delay tasks, and crash recovery. The project uses Python 3.10+.

## Build, Lint, and Test Commands

### Installation
```bash
# Basic install
pip install -e .

# With dev dependencies
pip install -e ".[dev]"

# With dashboard
pip install -e ".[dashboard]"
```

### Testing
```bash
# Run all tests
pytest

# Run single test file
pytest tests/test_queue.py

# Run single test
pytest tests/test_queue.py::TestSmartQueue::test_push_pop

# Run with verbose output
pytest -v

# Run specific test class
pytest tests/test_worker.py::TestWorker
```

### Linting
```bash
# Run ruff linter
ruff check qtask_list cli

# Auto-fix issues
ruff check --fix qtask_list cli
```

### Type Checking
```bash
# Run mypy
mypy qtask_list cli
```

### CLI Commands
```bash
# Set Redis URL
export REDIS_URL=redis://localhost:6379/0

# View queue status
python -m cli status stockev_list:fetch

# Real-time monitoring
python -m cli watch stockev_list:fetch -i 2

# Clear queue
python -m cli clear stockev_list:fetch --force

# Requeue DLQ tasks
python -m cli requeue stockev_list:fetch --force

# Start worker
python -m cli worker -q fetch -n stockev_list -w 4

# Start dashboard
python -m cli dashboard

# Monitor Redis memory
python -m cli monitor
```

## Code Style Guidelines

### Imports
- Standard library imports first, then third-party, then local
- Use explicit imports: `from typing import Optional, Dict, Any`
- Never use wildcard imports (`from x import *`)
- Group: stdlib → third-party → local (blank line between groups)

```python
import json
import time
import uuid
import redis
from typing import Optional, Any, Dict, List
from loguru import logger

from .history import TaskHistory
from .storage import RemoteStorage
```

### Formatting
- Line length: 100 characters (enforced by ruff)
- Use 4 spaces for indentation (no tabs)
- Use underscores for variable names: `queue_name`, `max_workers`
- Use PascalCase for class names: `SmartQueue`, `TaskHistory`
- Use SCREAMING_SNAKE_CASE for constants: `REDIS_URL`, `MAX_RETRIES`

### Type Hints
- Always use type hints for function parameters and return types
- Use `Optional[X]` instead of `X | None`
- Use explicit generic types: `Dict[str, Any]`, `List[str]`

```python
def push(self, payload: Dict[str, Any], delay_seconds: int = 0) -> str:
def get_stats(self) -> dict:
```

### Docstrings
- Use triple quotes for docstrings
- Include Args, Returns sections for complex functions

```python
def push(self, payload: Dict[str, Any], delay_seconds: int = 0) -> str:
    """
    Push a task to the queue.
    
    Args:
        payload: Task payload dict
        delay_seconds: Delay in seconds before task becomes available
    
    Returns:
        Task ID string
    """
```

### Error Handling
- Never use bare `except:` - catch specific exceptions
- Use `loguru` logger for logging (already imported as `logger`)
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

### Naming Conventions
- Classes: `PascalCase` (e.g., `SmartQueue`, `Worker`)
- Functions/methods: `snake_case` (e.g., `push`, `pop`)
- Variables: `snake_case` (e.g., `task_id`, `queue_name`)
- Constants: `UPPER_SNAKE_CASE` (e.g., `MAX_RETRIES`)
- Private methods: `_leading_underscore`

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

### Concurrency
- Use `ThreadPoolExecutor` for multi-threaded workers
- Use `threading.Semaphore` for backpressure
- Use `threading.Event` for shutdown signals

### Testing
- Place tests in `tests/` directory
- Use pytest fixtures for Redis connection
- Clear test data in fixtures

```python
@pytest.fixture
def r(redis_url):
    client = redis.from_url(redis_url, decode_responses=True)
    yield client
    keys = client.keys("testns:*")
    for k in keys:
        client.delete(k)
```

### Critical Patterns

#### Queue Key Structure
```
{namespace}:{queue_name}           - Main queue
{namespace}:{queue_name}:processing - Processing
{namespace}:{queue_name}:retry      - Retry queue
{namespace}:{queue_name}:dlq       - Dead Letter Queue
{namespace}:{queue_name}:delay     - Delay queue (sorted set)
```

#### Task Message Format
```python
{
    "task_id": "uuid-string",
    "payload": "json-string-of-payload"
}
```

#### Worker Startup Order (for pipelines)
1. Store worker (consumes final result queue)
2. Calculate worker (if applicable)
3. Fetch worker (produces to next queue)

### Common Issues to Avoid
1. Never use undefined variables (check logger is imported)
2. Always use timeout for HTTP requests
3. Use rpush for FIFO retry queues (not lpush)
4. Use atomic Lua scripts for multi-step operations
5. Pass redis_client for shared connections in multi-worker scenarios
