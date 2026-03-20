# qtask_list Reference

## Package Scope
- Python package: `qtask_list`
- Python requirement: `>=3.10`
- Core deps: `redis`, `loguru`, `typer`, `rich`, `requests`
- Public exports (`qtask_list/__init__.py`):
- `SmartQueue`
- `Worker`
- `RemoteStorage`
- `start_dashboard(port=8765, redis_url=...)`

## Core API

### SmartQueue
Constructor arguments:
- `redis_url: Optional[str]`
- `queue_name: str`
- `namespace: Optional[str]`
- `storage: Optional[RemoteStorage]`
- `large_threshold: int = 50 * 1024`
- `max_retry: int = 3`
- `ttl_days: int = 15`
- `redis_client: Optional[redis.Redis]`

Main methods:
- `push(payload, delay_seconds=0) -> str`
- `push_batch(payloads) -> List[str]`
- `pop(timeout=10) -> Optional[tuple]`
- `pop_no_wait() -> Optional[tuple]`
- `ack(raw_msg)`
- `fail(raw_msg, reason='')`
- `move_retry() -> int`
- `move_delay() -> int` (Lua atomic transfer)
- `recover() -> int`
- `get_stats() -> dict`
- `clear(include_dlq=True)`
- `requeue_dlq() -> int`

Task message shape:
```python
{"task_id": "uuid", "payload": "json-string"}
```

Queue keys:
```text
{namespace}:{queue_name}
{namespace}:{queue_name}:processing
{namespace}:{queue_name}:retry
{namespace}:{queue_name}:dlq
{namespace}:{queue_name}:delay
```

### Worker
Constructor highlights:
- `queue_name`, `namespace`, `max_workers`, `max_retry`
- optional `result_queue: SmartQueue`
- optional shared `redis_client`

Usage pattern:
1. Create `Worker(...)`
2. Register handlers via `@worker.on("action")`
3. `worker.run()`

Behavior notes:
- Pulls ready -> processing using `pop`.
- Success calls `ack`; failure calls `fail`.
- Runs maintenance loop for archive/monitor checks.
- Calls `recover()` on startup for crash recovery.

### TaskHistory
- Auto-managed by `SmartQueue.history`
- `record`, `update`, `get`, `list`, `clear`, `clean_expired`
- Redis keys: `qtask:task:{task_id}`, `qtask:hist:{queue_full_name}`

### RemoteStorage
- Used when payload exceeds `large_threshold`
- `save_bytes(data) -> key`
- `load(key) -> bytes`
- `delete(key)`
- Uses HTTP API under `api_base_url`.

## CLI Commands (`python -m cli`)

Common commands:
- `status [queue_name]`
- `watch <queue_name> -i 2`
- `clear <queue_name> --force`
- `requeue <queue_name> --force` (DLQ -> main)
- `retry <queue_name>` (retry -> main)
- `recover <queue_name>` (processing -> main)
- `history <queue_name> [-l 20] [-t task_id]`
- `worker -q <queue> -n <namespace> -w <workers> [-r result_queue]`
- `clean-history [queue_name] --ttl-days 15`
- `archive [queue_name] --days 1`
- `monitor`
- `dashboard --port 8765 --redis redis://...`

## Testing And Quality Commands
- `pytest`
- `pytest tests/test_queue.py`
- `pytest tests/test_worker.py::TestWorker`
- `ruff check qtask_list cli`
- `mypy qtask_list cli`

## Operational Gotchas
- In pipelines, start downstream/store worker before upstream/fetch worker.
- Missing handler action leads to retries and eventually DLQ.
- `retry` queue should preserve FIFO semantics via `rpush` on failure.
- Delay queue is a sorted set and must be periodically drained (`move_delay`).
- For shared infrastructure, pass `redis_client` explicitly across workers/queues.
- Always set timeout for outbound HTTP operations in handlers/storage clients.
