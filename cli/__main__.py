import importlib
import json
import os
import threading
import time
import webbrowser
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

import redis
import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

try:
    from qtask_list import SmartQueue, Worker

    QTASK_LIST_AVAILABLE = True
except ImportError:
    QTASK_LIST_AVAILABLE = False


DEFAULT_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


class QueueState(str, Enum):
    ready = "ready"
    processing = "processing"
    retry = "retry"
    dlq = "dlq"
    delay = "delay"


app = typer.Typer()
task_app = typer.Typer(help="查看和操作单个任务")
app.add_typer(task_app, name="task")
console = Console()


def get_redis(redis_url: str = DEFAULT_REDIS_URL) -> Any:
    """获取 Redis 连接"""
    return redis.from_url(redis_url, decode_responses=True)


def parse_queue_name(full_name: str) -> tuple[str, str]:
    """解析队列名称，返回 (namespace, queue_name)"""
    parts = full_name.split(":")
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], ":".join(parts[1:])


def normalize_queue_name(queue_name: str, namespace: Optional[str] = None) -> str:
    if namespace and ":" not in queue_name:
        return f"{namespace}:{queue_name}"
    return queue_name


def queue_from_name(
    redis_url: str,
    queue_name: str,
    namespace: Optional[str] = None,
    ttl_days: int = 15,
) -> SmartQueue:
    full_name = normalize_queue_name(queue_name, namespace)
    ns, q_name = parse_queue_name(full_name)
    return SmartQueue(redis_url, q_name, namespace=ns or None, ttl_days=ttl_days)


def parse_json_value(value: Any) -> Any:
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def task_record(r: Any, task_id: str) -> Optional[Dict[str, Any]]:
    key = f"qtask:task:{task_id}"
    rt = r.type(key)
    if rt == "hash":
        data = r.hgetall(key)
        if not data:
            return None
        return {k: parse_json_value(v) for k, v in data.items()}

    raw = r.get(key)
    if not raw:
        return None
    parsed = parse_json_value(raw)
    if isinstance(parsed, dict):
        return cast(Dict[str, Any], parsed)
    return {"_raw": raw}


def message_task_id(raw_msg: str) -> Optional[str]:
    try:
        data = json.loads(raw_msg)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    task_id = data.get("task_id")
    return str(task_id) if task_id else None


def decode_queue_message(raw_msg: str) -> Dict[str, Any]:
    item: Dict[str, Any] = {
        "task_id": "",
        "action": "",
        "retry": "",
        "payload": None,
        "raw": raw_msg,
    }
    try:
        data = json.loads(raw_msg)
    except (json.JSONDecodeError, TypeError) as exc:
        item["decode_error"] = str(exc)
        return item

    if not isinstance(data, dict):
        item["raw"] = data
        return item

    payload_raw = data.get("payload", {})
    payload = parse_json_value(payload_raw) if isinstance(payload_raw, str) else payload_raw

    item["task_id"] = data.get("task_id", "")
    item["payload"] = payload
    item["raw"] = data
    if isinstance(payload, dict):
        item["action"] = payload.get("action", "")
        item["retry"] = payload.get("_retry", "")
    return item


def json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def payload_summary(payload: Any, max_length: int = 100) -> str:
    raw = json.dumps(payload, ensure_ascii=False, default=str)
    if len(raw) <= max_length:
        return raw
    return raw[: max_length - 3] + "..."


def list_all_queues(r: Any) -> List[str]:
    """列出所有 qtask 队列 (安全版，使用 SCAN)"""
    queues = set()
    for key in r.scan_iter("qtask:hist:*"):
        queue_name = key.replace("qtask:hist:", "")
        queues.add(queue_name)

    for key in r.scan_iter("*"):
        if ":processing" in key or ":retry" in key or ":dlq" in key or ":delay" in key:
            continue
        if ":hist:" in key or ":task:" in key:
            continue
        try:
            if r.type(key) == "list":
                queues.add(key)
        except redis.RedisError:
            continue

    return sorted(queues)


def queue_bases_with_state_keys(r: Any) -> List[str]:
    queues = set(list_all_queues(r))
    for key in r.scan_iter("*:retry"):
        queues.add(key[: -len(":retry")])
    for key in r.scan_iter("*:dlq"):
        queues.add(key[: -len(":dlq")])
    for key in r.scan_iter("*:delay"):
        queues.add(key[: -len(":delay")])
    for key in r.scan_iter("*:processing"):
        queues.add(key[: -len(":processing")])
    for key in r.scan_iter("*:processing:*"):
        queues.add(key.split(":processing:", 1)[0])
    return sorted(queues)


def processing_keys(r: Any, queue_name: str) -> List[str]:
    """列出一个队列的 legacy 和 worker-specific processing keys。"""
    keys = {f"{queue_name}:processing"}
    keys.update(r.scan_iter(f"{queue_name}:processing:*"))
    return sorted(keys)


def state_keys(r: Any, queue_name: str, state: QueueState) -> List[str]:
    if state == QueueState.ready:
        return [queue_name]
    if state == QueueState.processing:
        return processing_keys(r, queue_name)
    if state == QueueState.retry:
        return [f"{queue_name}:retry"]
    if state == QueueState.dlq:
        return [f"{queue_name}:dlq"]
    if state == QueueState.delay:
        return [f"{queue_name}:delay"]
    raise ValueError(f"Unsupported queue state: {state}")


def get_queue_stats(r: Any, queue_name: str) -> dict:
    """获取队列统计"""
    base = queue_name
    return {
        "queue": int(r.llen(base)),
        "processing": sum(int(r.llen(key)) for key in processing_keys(r, base)),
        "retry": int(r.llen(f"{base}:retry")),
        "dlq": int(r.llen(f"{base}:dlq")),
        "delay": int(r.zcard(f"{base}:delay")),
    }


def read_state_messages(
    r: Any,
    queue_name: str,
    state: QueueState,
    limit: int,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if state == QueueState.delay:
        entries = r.zrange(f"{queue_name}:delay", 0, limit - 1, withscores=True)
        for raw_msg, score in entries:
            item = decode_queue_message(raw_msg)
            item["state"] = state.value
            item["source"] = f"{queue_name}:delay"
            item["run_at"] = datetime.fromtimestamp(score).isoformat(timespec="seconds")
            rows.append(item)
        return rows

    for key in state_keys(r, queue_name, state):
        remaining = limit - len(rows)
        if remaining <= 0:
            break
        messages = list(reversed(r.lrange(key, -remaining, -1)))
        for raw_msg in messages:
            item = decode_queue_message(raw_msg)
            item["state"] = state.value
            item["source"] = key
            rows.append(item)
    return rows


def print_message_rows(rows: List[Dict[str, Any]], title: str):
    if not rows:
        console.print("[yellow]No tasks found[/yellow]")
        return

    table = Table(title=title)
    table.add_column("State", style="cyan")
    table.add_column("Source", style="blue")
    table.add_column("Task ID", style="green", no_wrap=True)
    table.add_column("Action", style="magenta")
    table.add_column("Retry", justify="right")
    table.add_column("Payload")

    for item in rows:
        task_id = str(item.get("task_id") or "")
        table.add_row(
            str(item.get("state", "")),
            str(item.get("source", "")),
            task_id[:12] + ("..." if len(task_id) > 12 else ""),
            str(item.get("action", "")),
            str(item.get("retry", "")),
            payload_summary(item.get("payload")),
        )
    console.print(table)


def drain_list_to_ready(r: Any, source: str, queue_name: str) -> int:
    count = 0
    while True:
        msg = r.rpoplpush(source, queue_name)
        if not msg:
            break
        count += 1
    return count


def recover_processing_key(r: Any, processing_key: str, queue_name: str) -> int:
    return drain_list_to_ready(r, processing_key, queue_name)


def recover_stale_processing_cli(r: Any, queue_name: str) -> tuple[int, int]:
    count = recover_processing_key(r, f"{queue_name}:processing", queue_name)
    skipped = 0
    heartbeat_prefix = f"{queue_name}:worker:"

    for key in r.scan_iter(f"{queue_name}:processing:*"):
        worker_id = key.rsplit(":", 1)[-1]
        if r.exists(f"{heartbeat_prefix}{worker_id}"):
            skipped += int(r.llen(key))
            continue
        count += recover_processing_key(r, key, queue_name)
    return count, skipped


def move_exact_list_message(r: Any, source: str, destination: str, raw_msg: str) -> bool:
    lua_script = """
    local removed = redis.call('LREM', KEYS[1], 1, ARGV[1])
    if removed == 0 then
        return 0
    end
    redis.call('LPUSH', KEYS[2], ARGV[1])
    return removed
    """
    return bool(r.eval(lua_script, 2, source, destination, raw_msg))


def move_exact_delay_message(r: Any, source: str, destination: str, raw_msg: str) -> bool:
    lua_script = """
    local removed = redis.call('ZREM', KEYS[1], ARGV[1])
    if removed == 0 then
        return 0
    end
    redis.call('LPUSH', KEYS[2], ARGV[1])
    return removed
    """
    return bool(r.eval(lua_script, 2, source, destination, raw_msg))


def move_task_to_ready(
    r: Any,
    queue_name: str,
    task_id: str,
    source_state: QueueState,
) -> bool:
    if source_state == QueueState.ready:
        return False

    for key in state_keys(r, queue_name, source_state):
        if source_state == QueueState.delay:
            for raw_msg, _score in r.zscan_iter(key):
                if message_task_id(raw_msg) == task_id:
                    return move_exact_delay_message(r, key, queue_name, raw_msg)
        else:
            for raw_msg in r.lrange(key, 0, -1):
                if message_task_id(raw_msg) == task_id:
                    return move_exact_list_message(r, key, queue_name, raw_msg)
    return False


def remove_task_from_list_key(r: Any, key: str, task_id: str) -> int:
    removed = 0
    for raw_msg in r.lrange(key, 0, -1):
        if message_task_id(raw_msg) == task_id:
            removed += int(r.lrem(key, 0, raw_msg) or 0)
    return removed


def remove_task_from_delay_key(r: Any, key: str, task_id: str) -> int:
    removed = 0
    for raw_msg, _score in r.zscan_iter(key):
        if message_task_id(raw_msg) == task_id:
            removed += int(r.zrem(key, raw_msg) or 0)
    return removed


def remove_task_from_queues(
    r: Any,
    task_id: str,
    queue_name: Optional[str] = None,
) -> Dict[str, int]:
    queues = [queue_name] if queue_name else queue_bases_with_state_keys(r)
    queue_removed = 0

    for queue in queues:
        if not queue:
            continue
        for state in [
            QueueState.ready,
            QueueState.processing,
            QueueState.retry,
            QueueState.dlq,
        ]:
            for key in state_keys(r, queue, state):
                queue_removed += remove_task_from_list_key(r, key, task_id)
        queue_removed += remove_task_from_delay_key(r, f"{queue}:delay", task_id)

    history_deleted = int(r.delete(f"qtask:task:{task_id}") or 0)
    history_index_removed = 0
    for hist_key in r.scan_iter("qtask:hist:*"):
        history_index_removed += int(r.zrem(hist_key, task_id) or 0)

    return {
        "queue_messages": queue_removed,
        "history_records": history_deleted,
        "history_indexes": history_index_removed,
    }


def load_payload(payload: Optional[str], payload_file: Optional[Path]) -> Dict[str, Any]:
    if payload and payload_file:
        raise typer.BadParameter("Use either PAYLOAD or --file, not both")
    if payload_file:
        raw = payload_file.read_text(encoding="utf-8")
    elif payload:
        raw = payload
    else:
        raise typer.BadParameter("A JSON payload or --file is required")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"Payload must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("Payload must be a JSON object")
    return cast(Dict[str, Any], parsed)


def load_worker_from_module(module_spec: str) -> Worker:
    module_name, attr_name = (
        module_spec.split(":", 1) if ":" in module_spec else (module_spec, "worker")
    )
    module = importlib.import_module(module_name)
    loaded = getattr(module, attr_name)
    if not isinstance(loaded, Worker):
        raise typer.BadParameter(f"{module_spec} must resolve to a qtask_list.Worker instance")
    return loaded


@app.command()
def status(
    queue_name: Optional[str] = typer.Argument(None, help="队列名称，如 stockev:fetch"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
):
    """查看队列状态"""
    r = get_redis(redis_url)

    if queue_name:
        queue_name = normalize_queue_name(queue_name, namespace)
        stats = get_queue_stats(r, queue_name)

        table = Table(title=f"Queue: {queue_name}")
        table.add_column("Status", style="cyan")
        table.add_column("Count", style="green", justify="right")

        for key, value in stats.items():
            table.add_row(key, str(value))

        console.print(table)
        return

    queues = list_all_queues(r)

    if not queues:
        console.print("[yellow]No queues found[/yellow]")
        return

    table = Table(title="All Queues")
    table.add_column("Queue", style="cyan")
    table.add_column("Ready", style="green", justify="right")
    table.add_column("Processing", style="yellow", justify="right")
    table.add_column("Retry", style="magenta", justify="right")
    table.add_column("DLQ", style="red", justify="right")
    table.add_column("Delay", style="blue", justify="right")

    total = {"queue": 0, "processing": 0, "retry": 0, "dlq": 0, "delay": 0}

    for q in queues:
        try:
            stats = get_queue_stats(r, q)
            table.add_row(
                q,
                str(stats["queue"]),
                str(stats["processing"]),
                str(stats["retry"]),
                str(stats["dlq"]),
                str(stats["delay"]),
            )
            for k in total:
                total[k] += stats[k]
        except Exception as e:
            logger.error(f"Error getting stats for {q}: {e}")

    table.add_row(
        "[bold]Total[/bold]",
        f"[bold]{total['queue']}[/bold]",
        f"[bold]{total['processing']}[/bold]",
        f"[bold]{total['retry']}[/bold]",
        f"[bold]{total['dlq']}[/bold]",
        f"[bold]{total['delay']}[/bold]",
    )

    console.print(table)


@app.command()
def push(
    queue_name: str = typer.Argument(..., help="队列名称"),
    payload: Optional[str] = typer.Argument(None, help='JSON payload，如 {"action":"fetch"}'),
    payload_file: Optional[Path] = typer.Option(None, "--file", "-f", help="从 JSON 文件读取 payload"),
    delay_seconds: int = typer.Option(0, "--delay", "-d", help="延迟执行秒数"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
):
    """投递一个任务"""
    if not QTASK_LIST_AVAILABLE:
        console.print("[red]Error: qtask_list not installed. Run: pip install qtask_list[/red]")
        raise typer.Exit(1)

    payload_data = load_payload(payload, payload_file)
    q = queue_from_name(redis_url, queue_name, namespace)
    task_id = q.push(payload_data, delay_seconds=delay_seconds)
    result = {"queue": q.base, "task_id": task_id, "delay_seconds": delay_seconds}

    if json_output:
        console.print_json(json_dumps(result))
    else:
        console.print(f"[green]Pushed task {task_id} to {q.base}[/green]")


@app.command()
def peek(
    queue_name: str = typer.Argument(..., help="队列名称"),
    state: QueueState = typer.Option(QueueState.ready, "--state", "-s", help="队列状态"),
    limit: int = typer.Option(20, "--limit", "-l", help="显示条数"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
):
    """查看队列中的实际消息，不移动任务"""
    queue_name = normalize_queue_name(queue_name, namespace)
    r = get_redis(redis_url)
    rows = read_state_messages(r, queue_name, state, limit)

    if json_output:
        console.print_json(json_dumps(rows))
    else:
        print_message_rows(rows, f"{queue_name} [{state.value}]")


@app.command()
def clear(
    queue_name: str = typer.Argument(..., help="队列名称"),
    include_dlq: bool = typer.Option(True, "--include-dlq/--no-dlq", help="是否包含 DLQ"),
    include_history: bool = typer.Option(False, "--include-history", help="是否包含任务历史"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
    force: bool = typer.Option(False, "--force", "-f", help="强制执行"),
):
    """清空队列"""
    queue_name = normalize_queue_name(queue_name, namespace)
    if not force:
        console.print(f"[red]WARNING: This will clear queue {queue_name}[/red]")
        if include_history:
            console.print("[red]History records for this queue will also be removed[/red]")
        if not typer.confirm("Continue?"):
            raise typer.Abort()

    r = get_redis(redis_url)
    pipe = r.pipeline()
    pipe.delete(queue_name)
    for key in processing_keys(r, queue_name):
        pipe.delete(key)
    pipe.delete(f"{queue_name}:retry")
    pipe.delete(f"{queue_name}:delay")
    if include_dlq:
        pipe.delete(f"{queue_name}:dlq")
    pipe.execute()

    history_count = 0
    if include_history:
        q = queue_from_name(redis_url, queue_name)
        history_count = int(r.zcard(q.history.idx_key))
        q.history.clear()

    suffix = f" and {history_count} history records" if include_history else ""
    console.print(f"[green]Cleared {queue_name}{suffix}[/green]")


@app.command()
def requeue(
    queue_name: str = typer.Argument(..., help="队列名称"),
    task_id: Optional[str] = typer.Option(None, "--task-id", "-t", help="只重新入队一个任务"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
    force: bool = typer.Option(False, "--force", "-f", help="强制执行"),
):
    """将 DLQ 中的任务重新入队"""
    queue_name = normalize_queue_name(queue_name, namespace)
    if not force:
        target = f"task {task_id}" if task_id else "all tasks"
        console.print(f"[red]WARNING: This will requeue {target} from DLQ of {queue_name}[/red]")
        if not typer.confirm("Continue?"):
            raise typer.Abort()

    r = get_redis(redis_url)
    if task_id:
        moved = move_task_to_ready(r, queue_name, task_id, QueueState.dlq)
        if moved:
            queue_from_name(redis_url, queue_name).history.update(task_id, {"status": "pending"})
            console.print(f"[green]Requeued task {task_id} from DLQ[/green]")
        else:
            console.print(f"[yellow]Task {task_id} not found in DLQ[/yellow]")
            raise typer.Exit(1)
        return

    count = drain_list_to_ready(r, f"{queue_name}:dlq", queue_name)
    console.print(f"[green]Requeued {count} tasks from DLQ[/green]")


@app.command()
def retry(
    queue_name: str = typer.Argument(..., help="队列名称"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
):
    """将 retry 队列中的任务移回主队列"""
    queue_name = normalize_queue_name(queue_name, namespace)
    r = get_redis(redis_url)
    count = drain_list_to_ready(r, f"{queue_name}:retry", queue_name)
    console.print(f"[green]Moved {count} tasks from retry to main queue[/green]")


@app.command()
def recover(
    queue_name: str = typer.Argument(..., help="队列名称"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
    force_active: bool = typer.Option(
        False,
        "--force-active",
        help="也恢复仍有 heartbeat 的 active worker processing 队列",
    ),
):
    """恢复 processing 队列中的任务，默认只恢复 stale worker"""
    queue_name = normalize_queue_name(queue_name, namespace)
    r = get_redis(redis_url)

    if force_active:
        count = 0
        for processing in processing_keys(r, queue_name):
            count += recover_processing_key(r, processing, queue_name)
        console.print(f"[green]Recovered {count} tasks from all processing queues[/green]")
        return

    count, skipped = recover_stale_processing_cli(r, queue_name)
    console.print(f"[green]Recovered {count} tasks from stale processing queues[/green]")
    if skipped:
        console.print(f"[yellow]Skipped {skipped} active processing tasks[/yellow]")


@app.command()
def history(
    queue_name: Optional[str] = typer.Argument(None, help="队列名称"),
    limit: int = typer.Option(20, "--limit", "-l", help="显示条数"),
    task_id: Optional[str] = typer.Option(None, "--task-id", "-t", help="查看特定任务"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
):
    """查看任务历史记录"""
    r = get_redis(redis_url)

    if task_id:
        data = task_record(r, task_id)
        if data:
            console.print_json(json_dumps(data))
        else:
            console.print(f"[yellow]Task {task_id} not found[/yellow]")
        return

    if not queue_name:
        console.print("[red]QUEUE_NAME is required unless --task-id is provided[/red]")
        raise typer.Exit(1)

    queue_name = normalize_queue_name(queue_name, namespace)
    q = queue_from_name(redis_url, queue_name)
    tasks = q.history.list(limit=limit)

    if not tasks:
        console.print("[yellow]No history found[/yellow]")
        return

    table = Table(title=f"History: {queue_name}")
    table.add_column("Task ID", style="cyan", no_wrap=True)
    table.add_column("Action", style="green")
    table.add_column("Status", style="yellow")
    table.add_column("Created", style="blue")

    for data in tasks:
        tid = str(data.get("task_id", "unknown"))
        created = datetime.fromtimestamp(data.get("created_at", 0)).strftime("%H:%M:%S")
        status_style = {
            "pending": "yellow",
            "completed": "green",
            "failed": "red",
            "retry": "magenta",
        }.get(data.get("status", ""), "white")

        table.add_row(
            tid[:8] + "...",
            str(data.get("action", "")),
            f"[{status_style}]{data.get('status', '')}[/]",
            created,
        )

    console.print(table)


@app.command()
def watch(
    queue_name: str = typer.Argument(..., help="队列名称"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    interval: int = typer.Option(2, "--interval", "-i", help="刷新间隔(秒)"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
):
    """实时监控队列"""
    queue_name = normalize_queue_name(queue_name, namespace)

    r = get_redis(redis_url)
    console.print(f"[green]Watching {queue_name} (Ctrl+C to exit)[/green]")

    try:
        while True:
            stats = get_queue_stats(r, queue_name)
            console.clear()
            console.print(f"[cyan]Queue: {queue_name}[/cyan]")
            console.print(f"  Queue:        {stats['queue']}")
            console.print(f"  Processing:  {stats['processing']}")
            console.print(f"  Retry:        {stats['retry']}")
            console.print(f"  DLQ:          {stats['dlq']}")
            console.print(f"  Delay:        {stats['delay']}")
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[yellow]Stopped[/yellow]")


@app.command()
def worker(
    module: Optional[str] = typer.Option(
        None,
        "--module",
        "-m",
        help="导入一个 qtask_list.Worker 实例，如 myapp.workers:worker",
    ),
    queue: Optional[str] = typer.Option(None, "--queue", "-q", help="队列名称"),
    namespace: str = typer.Option("stockev", "--namespace", "-n", help="命名空间"),
    workers: int = typer.Option(1, "--workers", "-w", help="并发 worker 数"),
    result_queue: Optional[str] = typer.Option(None, "--result-queue", "-r", help="结果队列"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
):
    """启动用户代码中注册好 handler 的 Worker"""
    if not QTASK_LIST_AVAILABLE:
        console.print("[red]Error: qtask_list not installed. Run: pip install qtask_list[/red]")
        raise typer.Exit(1)

    if module:
        loaded_worker = load_worker_from_module(module)
        console.print(f"[green]Starting worker from {module}[/green]")
        loaded_worker.run()
        return

    if not queue:
        console.print("[red]Error: --module is required, or pass --queue for legacy validation[/red]")
        raise typer.Exit(1)

    console.print(
        "[red]Error: the generic CLI worker has no registered handlers. "
        "Create a Worker in your app and run: qtask worker --module myapp.workers:worker[/red]"
    )
    console.print(
        f"[yellow]Requested queue {namespace}:{queue} with {workers} workers was not started.[/yellow]"
    )
    if result_queue or redis_url:
        logger.debug("Legacy worker options were ignored because no handler module was provided")
    raise typer.Exit(1)


@app.command()
def clean_history(
    queue_name: Optional[str] = typer.Argument(None, help="队列名称，如 stockev_list:fetch"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    ttl_days: int = typer.Option(15, "--ttl-days", "-t", help="过期天数"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
):
    """清理过期历史记录"""
    if not QTASK_LIST_AVAILABLE:
        console.print("[red]Error: qtask_list not installed. Run: pip install qtask_list[/red]")
        raise typer.Exit(1)

    if queue_name:
        queue_name = normalize_queue_name(queue_name, namespace)
        q = queue_from_name(redis_url, queue_name, ttl_days=ttl_days)
        count = q.history.clean_expired()
        console.print(f"[green]Cleaned {count} expired history records from {queue_name}[/green]")
        return

    r = get_redis(redis_url)
    total = 0
    for hist_key in r.scan_iter("qtask:hist:*"):
        queue = hist_key.replace("qtask:hist:", "")
        q = queue_from_name(redis_url, queue, ttl_days=ttl_days)
        count = q.history.clean_expired()
        total += count

    console.print(f"[green]Cleaned {total} expired history records from all queues[/green]")


@app.command()
def archive(
    queue_name: Optional[str] = typer.Argument(None, help="队列名称"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    days: int = typer.Option(1, "--days", "-d", help="归档几天前的数据"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
):
    """将历史记录归档到 SQLite"""
    from qtask_list.archiver import ArchiveManager

    archiver = ArchiveManager(redis_url)

    if queue_name:
        queue_name = normalize_queue_name(queue_name, namespace)
        count = archiver.archive_to_sqlite(queue_name, days_ago=days)
        console.print(f"[green]Archived {count} tasks from {queue_name} to SQLite[/green]")
        return

    r = get_redis(redis_url)
    total = 0
    for hist_key in r.scan_iter("qtask:hist:*"):
        q_full = hist_key.replace("qtask:hist:", "")
        count = archiver.archive_to_sqlite(q_full, days_ago=days)
        total += count
    console.print(f"[green]Archived {total} tasks from all queues to SQLite[/green]")


@app.command()
def monitor(
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
):
    """查看 Redis 内存监控信息"""
    from qtask_list.archiver import Monitor

    r = get_redis(redis_url)
    m = Monitor(r)
    info = m.get_memory_info()

    table = Table(title="Redis Memory Monitor")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")

    for k, v in info.items():
        table.add_row(k, str(v))

    console.print(table)


@app.command()
def dashboard(
    host: str = typer.Option("127.0.0.1", "--host", help="Dashboard 监听地址，远程访问可用 0.0.0.0"),
    port: int = typer.Option(8765, "--port", "-p", help="Dashboard 端口"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
    user: Optional[str] = typer.Option(None, "--user", help="Dashboard 登录用户名"),
    password: Optional[str] = typer.Option(None, "--password", help="Dashboard 登录密码，设置后启用登录"),
    session_ttl: int = typer.Option(86400, "--session-ttl", help="登录会话有效期，秒"),
    secure_cookie: bool = typer.Option(False, "--secure-cookie", help="HTTPS 部署时启用 Secure Cookie"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="启动后打开浏览器"),
):
    """启动 Dashboard 面板"""
    os.environ["REDIS_URL"] = redis_url
    os.environ["QTASK_DASHBOARD_SESSION_TTL"] = str(session_ttl)
    if user is not None:
        os.environ["QTASK_DASHBOARD_USER"] = user
    if password is not None:
        os.environ["QTASK_DASHBOARD_PASSWORD"] = password
    if secure_cookie:
        os.environ["QTASK_DASHBOARD_SECURE_COOKIE"] = "1"

    if not QTASK_LIST_AVAILABLE:
        console.print("[red]Error: fastapi/uvicorn not installed. Run: pip install qtask_list[dashboard][/red]")
        raise typer.Exit(1)

    display_host = "localhost" if host in {"0.0.0.0", "::"} else host
    auth_enabled = bool(os.environ.get("QTASK_DASHBOARD_PASSWORD"))
    console.print(f"[green]Starting Dashboard on http://{display_host}:{port}[/green]")
    console.print(f"[cyan]Redis: {redis_url}[/cyan]")
    console.print(f"[cyan]Auth: {'enabled' if auth_enabled else 'disabled'}[/cyan]")
    console.print("Press Ctrl+C to stop\n")

    if open_browser:

        def open_browser_delayed():
            time.sleep(2)
            webbrowser.open(f"http://{display_host}:{port}")

        threading.Thread(target=open_browser_delayed, daemon=True).start()

    import uvicorn
    from dashboard.main import app

    uvicorn.run(app, host=host, port=port, log_level="info")


@task_app.command("get")
def task_get(
    task_id: str = typer.Argument(..., help="任务 ID"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
):
    """查看一个任务的历史详情"""
    r = get_redis(redis_url)
    data = task_record(r, task_id)
    if not data:
        console.print(f"[yellow]Task {task_id} not found[/yellow]")
        raise typer.Exit(1)
    console.print_json(json_dumps(data))


@task_app.command("requeue")
def task_requeue(
    task_id: str = typer.Argument(..., help="任务 ID"),
    queue_name: str = typer.Option(..., "--queue", "-q", help="队列名称"),
    source_state: QueueState = typer.Option(QueueState.dlq, "--from", help="来源状态"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
    force: bool = typer.Option(False, "--force", "-f", help="强制执行"),
):
    """将一个任务从指定状态移回 ready 队列"""
    if source_state == QueueState.ready:
        console.print("[red]--from ready is not a requeue operation[/red]")
        raise typer.Exit(1)

    queue_name = normalize_queue_name(queue_name, namespace)
    if not force:
        console.print(
            f"[red]WARNING: This will move task {task_id} "
            f"from {source_state.value} to ready in {queue_name}[/red]"
        )
        if not typer.confirm("Continue?"):
            raise typer.Abort()

    r = get_redis(redis_url)
    moved = move_task_to_ready(r, queue_name, task_id, source_state)
    if not moved:
        console.print(f"[yellow]Task {task_id} not found in {source_state.value}[/yellow]")
        raise typer.Exit(1)

    queue_from_name(redis_url, queue_name).history.update(task_id, {"status": "pending"})
    console.print(f"[green]Requeued task {task_id} from {source_state.value}[/green]")


@task_app.command("delete")
def task_delete(
    task_id: str = typer.Argument(..., help="任务 ID"),
    queue_name: Optional[str] = typer.Option(None, "--queue", "-q", help="限制在某个队列删除"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    redis_url: str = typer.Option(DEFAULT_REDIS_URL, "--redis", help="Redis URL"),
    force: bool = typer.Option(False, "--force", "-f", help="强制执行"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON"),
):
    """删除一个任务的队列消息和历史记录"""
    normalized_queue = normalize_queue_name(queue_name, namespace) if queue_name else None
    if not force:
        scope = normalized_queue or "all queues"
        console.print(f"[red]WARNING: This will delete task {task_id} from {scope}[/red]")
        if not typer.confirm("Continue?"):
            raise typer.Abort()

    r = get_redis(redis_url)
    result = remove_task_from_queues(r, task_id, normalized_queue)
    if json_output:
        console.print_json(json_dumps(result))
    else:
        console.print(
            "[green]Deleted "
            f"{result['queue_messages']} queue messages, "
            f"{result['history_records']} history records, "
            f"{result['history_indexes']} history index entries[/green]"
        )


if __name__ == "__main__":
    app()
