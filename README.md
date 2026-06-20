# qtask_list - 分布式任务队列

基于 Redis List 的分布式任务队列，核心机制是 `BRPOPLPUSH` 可靠消费 + 多子队列状态管理。仅依赖 Redis，无需 RabbitMQ/Kafka。

## 特性

- **可靠消费**：`BRPOPLPUSH` 原子操作，Worker 崩溃不丢任务
- **自动重试**：处理失败自动进入 retry 队列，超限进入 DLQ
- **延迟任务**：基于 Redis Sorted Set 的定时任务，Lua 脚本原子迁移
- **Crash Recovery**：Worker 意外退出后自动恢复 processing 中的任务
- **多级流水线**：通过 `result_queue` 串联多个 Worker，构建多阶段处理管道
- **大 payload 外存**：超过阈值自动走 RemoteStorage，避免撑爆 Redis
- **信号量背压**：线程池 Semaphore 防止任务排队无限增长
- **批量 Pipeline**：push_batch、历史查询、归档均使用 Redis Pipeline 减少 RTT
- **历史归档**：Redis 任务历史定期归档到 SQLite

## 安装

```bash
pip install -e .                  # 基础安装
pip install -e ".[dev]"           # 开发依赖 (pytest, ruff, mypy)
pip install -e ".[dashboard]"     # Dashboard 支持 (fastapi, uvicorn)
```

## 架构概览

### 整体架构

```
┌──────────────┐     ┌──────────────────────┐     ┌──────────────┐
│  Producer    │────▶│   Redis List Queue    │────▶│   Worker     │
│ (SmartQueue) │     │   ns:queue_name       │     │  (消费者)    │
│  push 任务   │     │                       │     │              │
└──────────────┘     │  :processing (处理中)  │     │   handler →  │
                     │  :retry      (重试)    │     │   ack/fail   │
                     │  :dlq        (死信)    │     │              │
                     │  :delay      (延迟)    │     └──────┬───────┘
                     └──────────────────────┘            │
                                                          ▼
                                                  ┌──────────────┐
                                                  │ result_queue │
                                                  │ (下一级队列) │
                                                  └──────────────┘
```

### 核心模块

| 文件 | 类 | 职责 |
|------|-----|------|
| `queue.py` | `SmartQueue` | 队列 CRUD，5 子队列管理，push/pop/ack/fail/recover/move_delay/move_retry |
| `worker.py` | `Worker` | 三线程模型（主循环 + 维护线程 + 线程池），handler 路由，并发处理 |
| `history.py` | `TaskHistory` | Redis ZSET + Hash 存储任务生命周期，TTL 自动过期清理 |
| `storage.py` | `RemoteStorage` | HTTP 客户端，大于 50KB 的 payload 自动外存 |
| `archiver.py` | `ArchiveManager` | Redis 任务历史 → SQLite 归档 |
| `archiver.py` | `Monitor` | Redis `INFO MEMORY` 内存监控 |
| `cli/__main__.py` | Typer CLI | 10 个子命令：status/watch/clear/requeue/retry/recover/history/worker/archive/monitor/dashboard |

### 队列结构

每条逻辑队列在 Redis 中对应 **5 个子队列**：

| 子队列 Key | 数据结构 | 用途 |
|-----------|---------|------|
| `{ns}:{name}` | List | 主队列，待消费任务 |
| `{ns}:{name}:processing` | List | 正在处理（Worker 取走后暂存，崩溃恢复用） |
| `{ns}:{name}:retry` | List | 重试队列（失败但未达 max_retry） |
| `{ns}:{name}:dlq` | List | 死信队列（重试耗尽） |
| `{ns}:{name}:delay` | Sorted Set | 延迟队列（按时间戳排序，Lua 脚本原子迁移） |

### 任务生命周期

```
push() ──▶ [主队列] ──▶ pop(BRPOPLPUSH) ──▶ [processing]
               │                                    │
               │                            ┌───────┴───────┐
               │                            ▼               ▼
               │                         ack()           fail()
               │                      (删除+记完成)   (删除+判断重试)
               │                                            │
               │                                  ┌─────────┴─────────┐
               │                                  ▼                   ▼
               │                            [retry 队列]         [dlq 队列]
               │                          retry < max_retry    retry >= max_retry
               │                                  │
               │                            move_retry()
               │                                  │
               └──────────────────────────────────┘
```

## 核心模块详解

### SmartQueue — 队列核心

```python
from qtask_list import SmartQueue

q = SmartQueue(
    redis_url="redis://localhost:6379/0",
    queue_name="fetch",
    namespace="stockev",
    max_retry=3,                # 最大重试次数
    large_threshold=50 * 1024,  # 大 payload 阈值 (50KB)
    ttl_days=15,                # 历史记录保留天数
)
```

**生产端 API：**

```python
# 单条任务
task_id = q.push({"action": "fetch_stock", "symbol": "AAPL"})

# 延迟任务 (60 秒后执行)
task_id = q.push({"action": "fetch_stock", "symbol": "AAPL"}, delay_seconds=60)

# 批量推送 (Redis Pipeline 优化)
task_ids = q.push_batch([
    {"action": "fetch_stock", "symbol": "AAPL"},
    {"action": "fetch_stock", "symbol": "TSLA"},
])
```

**消费端 API：**

```python
# 阻塞获取 (BRPOPLPUSH)
payload, raw_msg = q.pop(timeout=10)

# 非阻塞获取
payload, raw_msg = q.pop_no_wait()

# 确认 / 失败
q.ack(raw_msg)                    # 标记完成
q.fail(raw_msg, "error reason")   # 标记失败，自动判断重试或入 DLQ
```

**管理操作：**

```python
q.recover()        # Crash recovery: processing → 主队列
q.move_retry()     # retry 队列 → 主队列
q.move_delay()     # delay 到期 → 主队列 (Lua 原子操作)
q.requeue_dlq()    # DLQ → 主队列
q.clear()          # 清空所有子队列
q.get_stats()      # 返回 {"queue": N, "processing": N, "retry": N, "dlq": N, "delay": N}
```

**关键设计决策：**

- `pop()` 使用 `BRPOPLPUSH`（非 `BLPOP`），取任务的同时推入 `processing` 队列。Worker 崩溃后可通过 `recover()` 恢复。
- `move_delay()` 使用 Redis **Lua 脚本**，原子地将到期任务从 ZSET 迁移到主队列。
- 大 payload 自动外存：push 时超过 `large_threshold` 则上传到 `RemoteStorage`，队列中仅存引用。

### Worker — 任务处理器

**三线程模型：**

- **主线程 (`_worker_loop`)**：循环 pop 任务 → 提交到线程池
- **线程池 (`ThreadPoolExecutor`)**：并发执行 handler
- **维护线程 (`_maintenance_loop`)**：定时健康检查 + 历史归档

```python
from qtask_list import Worker, SmartQueue

# 创建下游队列 (可选)
store_q = SmartQueue("redis://localhost:6379/0", "store", namespace="stockev")

worker = Worker(
    redis_url="redis://localhost:6379/0",
    queue_name="fetch",
    namespace="stockev",
    result_queue=store_q,    # 处理结果自动推送到下游队列
    max_workers=4,           # 并发数
    max_retry=3,             # 最大重试
)

# 注册 handler — 按 payload["action"] 字段路由
@worker.on("fetch_stock")
def handle_fetch(task: dict):
    symbol = task["symbol"]
    price = fetch_price(symbol)
    # 返回 dict → 自动 push 到 result_queue
    return {"action": "store_price", "symbol": symbol, "price": price}

# 启动 (自动 crash recovery + 注册信号)
worker.run()
```

**关键设计决策：**

- **信号量背压**：`Semaphore(max_workers * 2)` 限制线程池排队深度，防止任务无限积压。
- **启动自动 recovery**：`run()` 立即调用 `q.recover()`，恢复上次崩溃遗留的 processing 任务。
- **优雅停止**：`SIGINT/SIGTERM` 信号 → `stop()` → 设置 `_shutdown_event` → 维护线程退出。
- **maintenance_interval**：默认 30 分钟执行一次归档和内存检查。

### TaskHistory — 任务历史

记录每个任务从创建到完成/失败的全生命周期状态：

```
Redis Key 结构:
  qtask:task:{task_id}     → Hash   (action, status, created_at, payload, ...)
  qtask:hist:{queue_name}  → ZSET   (时间戳索引 task_id)
```

- 每条记录和索引均设 `expire`（默认 15 天），过期由 `clean_expired()` 按 ZSET 分批清理。
- 同时兼容 Hash 和 String 两种存储格式，保证向后兼容。

### RemoteStorage — 大文件外存

解决 Redis 不适合存储大 payload 的问题：

```
push: payload > large_threshold (50KB)
  → save_bytes(data) → POST /api/storage/upload → 返回 key
  → 队列中只存 {"_large": true, "key": "xxx"}

pop: 检测到 _large=true
  → load(key) → GET /api/storage/download/{key} → 还原完整 payload
```

这是一个 HTTP 客户端，需外部存储服务配合。

### ArchiveManager + Monitor — 归档与监控

**ArchiveManager**：将 Redis 历史任务按日期归档到 SQLite：

```
归档流程:
  1. ZSET 取过期 task_id → 批量读取详情 (Pipeline)
  2. 按 created_at 日期分库 → archive_data/qtask_hist_{YYYYMMDD}.db
  3. 写入成功后从 Redis 删除
```

SQLite 表结构：`task_history(task_id, queue_name, action, status, payload, result, created_at, updated_at, raw_data)`

**Monitor**：`Redis INFO MEMORY` 监控，超阈值告警。

## 快速开始

### 1. 启动顺序 (重要!)

多级流水线场景必须先启动下游 Worker，再启动上游：

```
终端 1: python examples/03_store_worker.py   (先启动!)
终端 2: python examples/02_fetch_worker.py
终端 3: python examples/01_generator.py       (生产者)
```

原因：fetch worker 把结果 push 到 `ns:store` 队列，若无 store worker 消费则任务堆积。

### 2. Python SDK 示例

```python
from qtask_list import SmartQueue

q = SmartQueue(
    redis_url="redis://localhost:6379/0",
    queue_name="fetch",
    namespace="stockev_list"
)

# 单条任务
q.push({"action": "fetch_stock", "symbol": "AAPL", "url": "https://api.example.com/AAPL"})

# 批量任务
q.push_batch([
    {"action": "fetch_stock", "symbol": "AAPL"},
    {"action": "fetch_stock", "symbol": "TSLA"},
])

# 延迟任务 (60 秒后执行)
q.push({"action": "fetch_stock", "symbol": "AAPL"}, delay_seconds=60)
```

```python
from qtask_list import Worker, SmartQueue

store_q = SmartQueue("redis://localhost:6379/0", "store", namespace="stockev_list")

worker = Worker(
    redis_url="redis://localhost:6379/0",
    queue_name="fetch",
    namespace="stockev_list",
    result_queue=store_q,
    max_workers=4,
)

@worker.on("fetch_stock")
def handle_fetch(task):
    symbol = task["symbol"]
    price = fetch_price(symbol)
    return {"action": "store_price", "symbol": symbol, "price": price}

worker.run()
```

### 3. CLI 命令

```bash
# 查看所有队列状态
qtask status

# 查看指定队列
qtask status stockev_list:fetch

# 实时监控 (2 秒刷新)
qtask watch stockev_list:fetch -i 2

# 清空队列
qtask clear stockev_list:fetch --force

# DLQ 重新入队
qtask requeue stockev_list:fetch --force

# retry 队列 → 主队列
qtask retry stockev_list:fetch

# Crash recovery (processing → 主队列)
qtask recover stockev_list:fetch

# 查看历史
qtask history stockev_list:fetch
qtask history stockev_list:fetch -l 50

# 启动 Worker
qtask worker -q fetch -n stockev_list -w 4

# 清理过期历史
qtask clean-history stockev_list:fetch -t 15

# 归档到 SQLite
qtask archive stockev_list:fetch -d 1

# Redis 内存监控
qtask monitor

# 启动 Web Dashboard
qtask dashboard
```

## 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `redis_url` | `redis://localhost:6379/0` | Redis 连接地址 |
| `namespace` | `""` | 命名空间，多项目隔离 |
| `max_retry` | `3` | 最大重试次数，超限进入 DLQ |
| `large_threshold` | `50KB` | 大 payload 阈值，超限走 RemoteStorage |
| `ttl_days` | `15` | 历史记录保留天数 |
| `max_workers` | `1` | Worker 线程池并发数 |
| `maintenance_interval` | `1800` (30min) | 维护线程执行间隔（归档+健康检查） |

## 环境变量

```bash
export REDIS_URL=redis://localhost:6379/0
```

## 项目结构

```
qtask_list/
├── qtask_list/           # 核心库
│   ├── __init__.py       # 公开 API: SmartQueue, Worker, RemoteStorage
│   ├── queue.py          # SmartQueue — 5 子队列管理
│   ├── worker.py         # Worker — 三线程任务处理器
│   ├── history.py        # TaskHistory — Redis 任务历史
│   ├── storage.py        # RemoteStorage — 大文件 HTTP 外存
│   └── archiver.py       # ArchiveManager (SQLite 归档) + Monitor (内存监控)
├── cli/
│   └── __main__.py       # Typer CLI (qtask / qtask_list 命令)
├── dashboard/
│   ├── main.py           # FastAPI Web Dashboard
│   └── templates/        # Jinja2 模板
├── examples/             # 使用示例
├── tests/                # 测试用例
├── pyproject.toml        # 项目配置与依赖
└── README.md
```
