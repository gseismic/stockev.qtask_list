---
name: pai-qtask-list-codexskill-oc
description: Comprehensive user guide for qtask_list distributed queue library. Use when building queue producers/consumers, implementing workers with SmartQueue and Worker classes, creating multi-stage pipelines, or needing CLI operations (status, watch, clear, recover, requeue). Covers all classes (SmartQueue, Worker, TaskHistory, RemoteStorage, ArchiveManager, Monitor), core patterns (retry, DLQ, delay, crash recovery), and complete working examples.
---

# qtask_list User Guide

## Overview

qtask_list is a distributed task queue library based on Redis List, supporting:
- **Retry** - Automatic retry with configurable max attempts
- **DLQ (Dead Letter Queue)** - Failed tasks after max retries
- **Delay** - Scheduled/delayed task execution
- **Crash Recovery** - Automatic recovery of in-progress tasks
- **Multi-stage Pipeline** - result_queue for chained workers

## Quick Start

### 1. Installation

```bash
pip install -e .
pip install -e ".[dev]"     # with dev dependencies
pip install -e ".[dashboard]" # with dashboard
```

### 2. Set Redis URL

```bash
export REDIS_URL=redis://localhost:6379/0
```

### 3. Basic Producer/Consumer

**Producer (push tasks):**
```python
from qtask_list import SmartQueue

q = SmartQueue("redis://localhost:6379/0", "myqueue", namespace="myapp")
task_id = q.push({"action": "process", "data": "hello"})
```

**Consumer (worker):**
```python
from qtask_list import Worker

worker = Worker("redis://localhost:6379/0", "myqueue", namespace="myapp")

@worker.on("process")
def process_task(task):
    print(f"Processing: {task}")
    return {"status": "done"}

worker.run()
```

## Core Concepts

### Queue Key Structure
```
{namespace}:{queue_name}           - Main queue
{namespace}:{queue_name}:processing - Currently processing
{namespace}:{queue_name}:retry      - Retry queue (FIFO)
{namespace}:{queue_name}:dlq        - Dead Letter Queue
{namespace}:{queue_name}:delay     - Delay queue (sorted set)
```

### Task Flow
1. `push()` → main queue
2. `pop()` → moves to processing queue
3. `ack()` → removes from processing (success)
4. `fail()` → increments retry counter; if max_retry reached → DLQ

### Delay Tasks
```python
# Task executes after 60 seconds
q.push({"action": "send_email"}, delay_seconds=60)
```

### Crash Recovery
Worker calls `recover()` on startup to move tasks from `:processing` back to main queue.

## Class Reference

### SmartQueue
Main queue class for push/pop/ack/fail operations. Source: `qtask_list/queue.py`

### Worker
Event-driven task processor with handler registration. Source: `qtask_list/worker.py`

### TaskHistory
Task history tracking with TTL. Source: `qtask_list/history.py`

### RemoteStorage
Large payload storage (50KB+ threshold). Source: `qtask_list/storage.py`

### ArchiveManager
SQLite archiver for historical tasks. Source: `qtask_list/archiver.py`

### Monitor
Redis memory health monitor. Source: `qtask_list/archiver.py`

## Detailed Documentation

- **Class API Reference**: See [references/classes.md](references/classes.md)
- **Working Examples**: See [references/examples.md](references/examples.md)
- **CLI Commands**: See [references/cli-commands.md](references/cli-commands.md)
- **Core Source Files**: See [references/source-files.md](references/source-files.md)

## CLI Operations

```bash
# View queue status
python -m cli status myapp:myqueue

# Real-time monitoring
python -m cli watch myapp:myqueue -i 2

# Clear queue
python -m cli clear myapp:myqueue --force

# Requeue DLQ tasks
python -m cli requeue myapp:myqueue --force

# Start worker
python -m cli worker -q myqueue -n myapp -w 4

# Start dashboard
python -m cli dashboard
```

## Testing

```bash
pytest tests/test_queue.py
pytest tests/test_worker.py
pytest -v
```