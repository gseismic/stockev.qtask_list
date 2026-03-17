import os
import sys
import redis
import typer
import time
import json
import signal
import threading
import webbrowser
from datetime import datetime
from typing import Optional, List, Dict, Any
from rich.console import Console
from rich.table import Table
from loguru import logger

try:
    from qtask_list import Worker, SmartQueue
    QTASK_LIST_AVAILABLE = True
except ImportError:
    QTASK_LIST_AVAILABLE = False

app = typer.Typer()
console = Console()


def get_redis() -> redis.Redis:
    """获取 Redis 连接"""
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return redis.from_url(url, decode_responses=True)


def parse_queue_name(full_name: str) -> tuple:
    """解析队列名称，返回 (namespace, queue_name)"""
    parts = full_name.split(":")
    if len(parts) == 1:
        return "", parts[0]
    elif len(parts) >= 2:
        return parts[0], ":".join(parts[1:])
    return "", full_name


def list_all_queues(r: redis.Redis) -> List[str]:
    """列出所有 qtask 队列 (安全版，使用 SCAN)"""
    queues = set()
    # 使用 scan_iter 代替 keys("*")
    for key in r.scan_iter("qtask:hist:*"):
        queue_name = key.replace("qtask:hist:", "")
        queues.add(queue_name)
    
    # 也扫描 list 类型
    for key in r.scan_iter("*"):
        if ":processing" in key or ":retry" in key or ":dlq" in key or ":delay" in key:
            continue
        if ":hist:" in key or ":task:" in key:
            continue
        try:
            if r.type(key) == "list":
                queues.add(key)
        except:
            continue
            
    return sorted(queues)


def get_queue_stats(r: redis.Redis, queue_name: str) -> dict:
    """获取队列统计"""
    base = queue_name
    return {
        "queue": r.llen(base),
        "processing": r.llen(f"{base}:processing"),
        "retry": r.llen(f"{base}:retry"),
        "dlq": r.llen(f"{base}:dlq"),
        "delay": r.zcard(f"{base}:delay"),
    }


@app.command()
def status(
    queue_name: Optional[str] = typer.Argument(None, help="队列名称，如 stockev:fetch"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    redis_url: str = typer.Option("redis://localhost:6379/0", "--redis", help="Redis URL"),
):
    """查看队列状态"""
    r = redis.from_url(redis_url, decode_responses=True)

    if queue_name:
        if namespace and ":" not in queue_name:
            queue_name = f"{namespace}:{queue_name}"
        stats = get_queue_stats(r, queue_name)
        
        table = Table(title=f"Queue: {queue_name}")
        table.add_column("Status", style="cyan")
        table.add_column("Count", style="green", justify="right")
        
        for key, value in stats.items():
            table.add_row(key, str(value))
        
        console.print(table)
        
    else:
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
def clear(
    queue_name: str = typer.Argument(..., help="队列名称"),
    include_dlq: bool = typer.Option(True, "--include-dlq/--no-dlq", help="是否包含 DLQ"),
    force: bool = typer.Option(False, "--force", "-f", help="强制执行"),
):
    """清空队列"""
    if not force:
        console.print(f"[red]WARNING: This will clear queue {queue_name}[/red]")
        if not typer.confirm("Continue?"):
            raise typer.Abort()
    
    r = get_redis()
    pipe = r.pipeline()
    pipe.delete(queue_name)
    pipe.delete(f"{queue_name}:processing")
    pipe.delete(f"{queue_name}:retry")
    pipe.delete(f"{queue_name}:delay")
    if include_dlq:
        pipe.delete(f"{queue_name}:dlq")
    pipe.execute()
    
    console.print(f"[green]Cleared {queue_name}[/green]")


@app.command()
def requeue(
    queue_name: str = typer.Argument(..., help="队列名称"),
    force: bool = typer.Option(False, "--force", "-f", help="强制执行"),
):
    """将 DLQ 中的任务重新入队"""
    if not force:
        console.print(f"[red]WARNING: This will requeue all tasks from DLQ of {queue_name}[/red]")
        if not typer.confirm("Continue?"):
            raise typer.Abort()
    
    r = get_redis()
    dlq = f"{queue_name}:dlq"
    
    count = 0
    while True:
        msg = r.rpoplpush(dlq, queue_name)
        if not msg:
            break
        count += 1
    
    console.print(f"[green]Requeued {count} tasks from DLQ[/green]")


@app.command()
def retry(
    queue_name: str = typer.Argument(..., help="队列名称"),
):
    """将 retry 队列中的任务移回主队列"""
    r = get_redis()
    retry_q = f"{queue_name}:retry"
    
    count = 0
    while True:
        msg = r.rpoplpush(retry_q, queue_name)
        if not msg:
            break
        count += 1
    
    console.print(f"[green]Moved {count} tasks from retry to main queue[/green]")


@app.command()
def recover(
    queue_name: str = typer.Argument(..., help="队列名称"),
):
    """将 processing 队列中的任务移回主队列（Crash recovery）"""
    r = get_redis()
    processing = f"{queue_name}:processing"
    
    count = 0
    while True:
        msg = r.rpoplpush(processing, queue_name)
        if not msg:
            break
        count += 1
    
    console.print(f"[green]Recovered {count} tasks from processing queue[/green]")


@app.command()
def history(
    queue_name: str = typer.Argument(..., help="队列名称"),
    limit: int = typer.Option(20, "--limit", "-l", help="显示条数"),
    task_id: Optional[str] = typer.Option(None, "--task-id", "-t", help="查看特定任务"),
):
    """查看任务历史记录"""
    r = get_redis()
    
    if task_id:
        key = f"qtask:task:{task_id}"
        rt = r.type(key)
        if rt == "hash":
            data = r.hgetall(key)
            if data:
                for k, v in data.items():
                    try:
                        data[k] = json.loads(v)
                    except: pass
                console.print_json(json.dumps(data, indent=2))
                return
        else:
            raw = r.get(key)
            if raw:
                console.print_json(raw)
                return
        
        console.print(f"[yellow]Task {task_id} not found[/yellow]")
        return
    
    from qtask_list import SmartQueue
    full_name = queue_name.split(":")
    ns = full_name[0] if len(full_name) > 1 else ""
    q_name = full_name[-1]
    
    q = SmartQueue(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), q_name, namespace=ns)
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
        tid = data.get("task_id", "unknown")
        created = datetime.fromtimestamp(data.get("created_at", 0)).strftime("%H:%M:%S")
        status_style = {
            "pending": "yellow",
            "completed": "green",
            "failed": "red",
            "retry": "magenta",
        }.get(data.get("status", ""), "white")
        
        table.add_row(
            tid[:8] + "...",
            data.get("action", ""),
            f"[{status_style}]{data.get('status', '')}[/]",
            created,
        )
    
    console.print(table)


@app.command()
def watch(
    queue_name: str = typer.Argument(..., help="队列名称"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    interval: int = typer.Option(2, "--interval", "-i", help="刷新间隔(秒)"),
    redis_url: str = typer.Option("redis://localhost:6379/0", "--redis", help="Redis URL"),
):
    """实时监控队列"""
    if namespace and ":" not in queue_name:
        queue_name = f"{namespace}:{queue_name}"
    
    r = redis.from_url(redis_url, decode_responses=True)
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
    queue: str = typer.Option(..., "--queue", "-q", help="队列名称"),
    namespace: str = typer.Option("stockev", "--namespace", "-n", help="命名空间"),
    workers: int = typer.Option(1, "--workers", "-w", help="并发 worker 数"),
    result_queue: Optional[str] = typer.Option(None, "--result-queue", "-r", help="结果队列"),
    redis_url: str = typer.Option("redis://localhost:6379/0", "--redis", help="Redis URL"),
):
    """启动 Worker"""
    if not QTASK_LIST_AVAILABLE:
        console.print("[red]Error: qtask_list not installed. Run: pip install qtask_list[/red]")
        raise typer.Exit(1)
    
    ns = namespace if namespace else None
    result_q = None
    if result_queue:
        result_q = SmartQueue(redis_url, result_queue, namespace=ns)
    
    w = Worker(
        redis_url,
        queue,
        namespace=ns,
        result_queue=result_q,
        max_workers=workers,
    )
    
    console.print(f"[green]Starting worker for {namespace}:{queue} with {workers} workers[/green]")
    w.run()


@app.command()
def clean_history(
    queue_name: Optional[str] = typer.Argument(None, help="队列名称，如 stockev_list:fetch"),
    namespace: Optional[str] = typer.Option(None, "--namespace", "-n", help="命名空间"),
    ttl_days: int = typer.Option(15, "--ttl-days", "-t", help="过期天数"),
    redis_url: str = typer.Option("redis://localhost:6379/0", "--redis", help="Redis URL"),
):
    """清理过期历史记录"""
    if not QTASK_LIST_AVAILABLE:
        console.print("[red]Error: qtask_list not installed. Run: pip install qtask_list[/red]")
        raise typer.Exit(1)
    
    if queue_name and namespace and ":" not in queue_name:
        queue_name = f"{namespace}:{queue_name}"
    
    from qtask_list import SmartQueue
    
    if queue_name:
        full_name = queue_name.split(":")
        ns = full_name[0] if len(full_name) > 1 else ""
        q_name = full_name[-1]
        
        q = SmartQueue(redis_url, q_name, namespace=ns, ttl_days=ttl_days)
        count = q.history.clean_expired()
        console.print(f"[green]Cleaned {count} expired history records from {queue_name}[/green]")
    else:
        r = redis.from_url(redis_url, decode_responses=True)
        total = 0
        for hist_key in r.scan_iter("qtask:hist:*"):
            queue = hist_key.replace("qtask:hist:", "")
            full_name = queue.split(":")
            ns = full_name[0] if len(full_name) > 1 else ""
            q_name = full_name[-1]
            
            q = SmartQueue(redis_url, q_name, namespace=ns, ttl_days=ttl_days)
            count = q.history.clean_expired()
            total += count
        
        console.print(f"[green]Cleaned {total} expired history records from all queues[/green]")


@app.command()
def archive(
    queue_name: Optional[str] = typer.Argument(None, help="队列名称"),
    days: int = typer.Option(1, "--days", "-d", help="归档几天前的数据"),
    redis_url: str = typer.Option("redis://localhost:6379/0", "--redis", help="Redis URL"),
):
    """将历史记录归档到 SQLite"""
    from qtask_list.archiver import ArchiveManager
    archiver = ArchiveManager(redis_url)
    
    if queue_name:
        count = archiver.archive_to_sqlite(queue_name, days_ago=days)
        console.print(f"[green]Archived {count} tasks from {queue_name} to SQLite[/green]")
    else:
        r = redis.from_url(redis_url, decode_responses=True)
        total = 0
        for hist_key in r.scan_iter("qtask:hist:*"):
            q_full = hist_key.replace("qtask:hist:", "")
            count = archiver.archive_to_sqlite(q_full, days_ago=days)
            total += count
        console.print(f"[green]Archived {total} tasks from all queues to SQLite[/green]")


@app.command()
def monitor(
    redis_url: str = typer.Option("redis://localhost:6379/0", "--redis", help="Redis URL"),
):
    """查看 Redis 内存监控信息"""
    from qtask_list.archiver import Monitor
    r = redis.from_url(redis_url, decode_responses=True)
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
    port: int = typer.Option(8765, "--port", "-p", help="Dashboard 端口"),
    redis_url: str = typer.Option("redis://localhost:6379/0", "--redis", help="Redis URL"),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="启动后打开浏览器"),
):
    """启动 Dashboard 面板"""
    os.environ["REDIS_URL"] = redis_url
    
    if not QTASK_LIST_AVAILABLE:
        console.print("[red]Error: fastapi/uvicorn not installed. Run: pip install qtask_list[dashboard][/red]")
        raise typer.Exit(1)
    
    console.print(f"[green]Starting Dashboard on http://localhost:{port}[/green]")
    console.print(f"[cyan]Redis: {redis_url}[/cyan]")
    console.print("Press Ctrl+C to stop\n")
    
    if open_browser:
        def open_browser_delayed():
            time.sleep(2)
            webbrowser.open(f"http://localhost:{port}")
        threading.Thread(target=open_browser_delayed, daemon=True).start()
    
    import uvicorn
    from dashboard.main import app
    
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    app()
