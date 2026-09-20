# qtask_list - 分布式任务队列

基于 Redis List 的分布式任务队列，核心机制是 `BRPOPLPUSH` 可靠消费 + 多子队列状态管理。仅依赖 Redis，无需 RabbitMQ/Kafka。

## 特性

- **可靠消费**：`BRPOPLPUSH` 原子操作，Worker 崩溃不丢任务
- **重试退避**：失败按指数退避（30s 起步，±10% 抖动）写入延迟队列，不再瞬时烧光重试次数
- **业务去重**：`logical_key` 身份键（SETNX+TTL），动态列表重复投递零成本防重
- **执行截止**：`expire_seconds` 写入消息信封，pop 时强制检查，过期任务跳过不执行（历史标记 `skipped`）
- **延迟任务**：基于 Redis Sorted Set 的定时任务，Lua 脚本原子迁移（单次批量上限 500）
- **Crash Recovery**：Worker 意外退出后自动恢复 processing 中的任务；维护线程周期性接手失联 Worker 的任务
- **多级流水线**：通过 `result_queue` 串联多个 Worker，构建多阶段处理管道
- **大 payload 外存**：超过阈值自动走 RemoteStorage，避免撑爆 Redis；瞬时拉取失败延后重试不进 DLQ
- **信号量背压**：线程池 Semaphore 防止任务排队无限增长
- **批量 Pipeline**：push_batch、历史查询、归档均使用 Redis Pipeline 减少 RTT
- **历史归档**：仅 terminal 状态（completed/failed/skipped）的任务归档到 SQLite，live 任务历史保留 Redis

## 安装

```bash
pip install -e .                  # 基础安装
pip install -e ".[dev]"           # 开发依赖 (pytest, ruff, mypy)
pip install -e ".[dashboard]"     # Dashboard 支持 (fastapi, uvicorn)
pip install -e ".[storage]"       # RemoteStorage 服务端支持
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
| `cli/__main__.py` | Typer CLI | 任务生命周期控制台：push/peek/status/requeue/retry/recover/history/task/worker/archive/monitor/dashboard |

### 队列结构

每条逻辑队列在 Redis 中对应主队列和若干状态队列：

| 子队列 Key | 数据结构 | 用途 |
|-----------|---------|------|
| `{ns}:{name}` | List | 主队列，待消费任务 |
| `{ns}:{name}:processing` | List | 默认 processing 队列（SmartQueue 直接使用 / 兼容旧版本） |
| `{ns}:{name}:processing:{worker_id}` | List | Worker 专属 processing 队列，避免多 Worker 启动时误恢复活跃任务 |
| `{ns}:{name}:retry` | List | 重试队列（失败但未达 max_retry） |
| `{ns}:{name}:dlq` | List | 死信队列（重试耗尽） |
| `{ns}:{name}:delay` | Sorted Set | 延迟队列（按时间戳排序，Lua 脚本原子迁移） |
| `{ns}:{name}:worker:{worker_id}` | String + TTL | Worker heartbeat，用于判断 processing 是否已失联 |

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

# 业务身份键去重：同键任务存在时跳过投递并返回 None
task_id = q.push(
    {"action": "fetch_kline", "symbol": "AAPL"},
    logical_key="kline:AAPL:1d:2026-09-19",  # 身份键：类型:主体:时间桶
    expire_seconds=7 * 86400,                  # 执行截止：超期未执行则 pop 时跳过
)
task_id = q.push(..., logical_key="kline:AAPL:1d:2026-09-19", force=True)  # 显式绕过去重

# 批量推送 (Redis Pipeline 优化)；支持 delay/expire/logical_keys，被去重的项返回 None
task_ids = q.push_batch(
    [
        {"action": "fetch_news", "url": "https://.../a"},
        {"action": "fetch_news", "url": "https://.../b"},
    ],
    logical_keys=["news:sha(a)", "news:sha(b)"],
)
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
q.move_retry()     # retry 队列 → 主队列（仅 retry_backoff_base=0 的旧路径使用）
q.move_delay()     # delay 到期 → 主队列 (Lua 原子操作，单次最多 500 条)
q.requeue_dlq()    # DLQ → 主队列，默认重置重试计数（reset_retry=True）
q.clear()          # 清空所有子队列
q.get_stats()      # 返回 {"queue": N, "processing": N, "retry": N, "dlq": N, "delay": N}
```

**关键设计决策：**

- `pop()` 使用 `BRPOPLPUSH`（非 `BLPOP`），取任务的同时推入 `processing` 队列。Worker 使用带 heartbeat 的专属 processing key，只自动恢复已失联 Worker 的任务。
- 重试退避：`fail()` 未耗尽重试次数时按 `retry_backoff_base * 2^(retry-1)`（±10% 抖动，上限 `retry_backoff_max`）写入 delay ZSET，到点由 `move_delay()` 迁回主队列。`retry_backoff_base=0` 恢复旧的立即重试行为。带退避的重试任务在 delay 视图可见（payload 携带 `_retry`），历史状态为 `retry`。
- 执行截止：`expire_seconds` 写入消息信封 `expires_at`，pop 时强制检查；过期任务直接丢弃并标记历史 `skipped`，不进 DLQ（重放也无意义）。旧格式消息无此字段则不强制。
- `move_delay()` 使用 Redis **Lua 脚本**，原子地将到期任务从 ZSET 迁移到主队列，单次调用最多迁移 500 条，避免同一秒大量任务到期时阻塞 Redis。
- 大 payload 自动外存：push 时超过 `large_threshold` 则上传到 `RemoteStorage`，队列中仅存引用。消费端未配置 storage 时显式报错进 DLQ（配置错误）；storage 瞬时不可用时任务移入 delay 稍后重试，不进 DLQ。

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
- **启动自动 recovery**：`run()` 只恢复 heartbeat 已过期的 `processing:{worker_id}`，避免抢回其他活跃 Worker 正在处理的任务。
- **优雅停止**：`SIGINT/SIGTERM` 信号 → `stop()`；线程池 drain 期间继续刷新 heartbeat，任务处理完成后再清理 Worker 状态。
- **maintenance_interval**：默认 30 分钟执行一次归档和内存检查。

### QueueAdmin — 管理接口

`QueueAdmin` 是给 Dashboard、CLI、Agent 和运维脚本复用的任务生命周期控制接口。它使用用户能理解的状态和动作，不要求调用方直接拼 Redis Key。

```python
from qtask_list import QueueAdmin, QueueState

admin = QueueAdmin("redis://localhost:6379/0")

# 查看队列和任务
queues = admin.list_queues()
tasks = admin.list_tasks("stockev:day-kline:fetch", state="dlq", limit=50)
history = admin.list_tasks("stockev:day-kline:fetch", state=QueueState.history, limit=50)
completed = admin.list_tasks("stockev:day-kline:fetch", state=QueueState.completed, limit=50)
failed = admin.list_tasks("stockev:day-kline:fetch", state=QueueState.failed, limit=50)
expired = admin.list_tasks("stockev:day-kline:fetch", state=QueueState.expired, limit=50)
recent_done = admin.list_tasks(
    "stockev:day-kline:fetch",
    state=QueueState.completed,
    completed_after=1717200000,
)
detail = admin.get_task("<task_id>")

# 手动控制
admin.push_task("stockev:day-kline:fetch", {"action": "test"})
admin.push_task("stockev:day-kline:fetch", {"action": "test"}, expire_seconds=3600)
admin.requeue_task("stockev:day-kline:fetch", "<task_id>", from_state="dlq")
admin.requeue_dlq("stockev:day-kline:fetch")
admin.list_expired("stockev:day-kline:fetch", limit=100)
admin.requeue_expired("stockev:day-kline:fetch")
admin.move_retry("stockev:day-kline:fetch")
admin.recover("stockev:day-kline:fetch")  # 默认只恢复 stale worker

# 清理、删除和诊断
admin.clear_queue("stockev:day-kline:fetch", include_history=True)
admin.clean_history("stockev:day-kline:fetch", ttl_days=15)
admin.delete_task("<task_id>", queue_name="stockev:day-kline:fetch")
admin.delete_queue("stockev:day-kline:fetch")  # 删除整条队列及历史，谨慎使用
admin.diagnose("stockev:day-kline:fetch")
```

`QueueState` 用户可见状态：`ready`, `processing`, `retry`, `dlq`, `delay`, `history`, `completed`, `failed`, `skipped`, `expired`, `all`。

`expired` 表示业务任务过期：投递时通过 `expire_seconds` 写入 `expires_at`，任务未完成且超过该时间后会出现在过期视图中；`clean-history` / `clean_expired()` 表示历史记录 TTL 清理，二者不是同一件事。

`skipped` 表示任务在 pop 时已过执行截止（`expires_at`），被跳过未执行——与 `expired` 视图（历史标记角度）配合使用。

DLQ 重放（`admin.requeue_dlq` / `qtask requeue`）默认剥离消息中的 `_retry` 计数：人工重放视为全新尝试，可完整走完重试预算；保留原计数请传 `reset_retry=False`（CLI `--keep-retry`）。从活跃 Worker 的 processing 重放会被拒绝，需先执行 recover。

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

这是一个 HTTP 客户端，需外部存储服务配合。项目内置了一个轻量服务端，可通过 CLI 启动：

```bash
qtask storage --port 8096 --data-dir ~/.qtask-storage --ttl-days 7
```

服务端依赖安装：

```bash
pip install -e ".[storage]"
```

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

## 任务身份与去重（logical_key）

动态增长的抓取列表（新闻等）和定时抓取场景需要"同一业务对象不重复投递"。规则：

```
logical_key = 任务类型 : 业务主体 : 数据时间桶
```

- **显式声明**：身份键由调用方传入，库不做 payload 自动哈希（URL 带时间戳/query 参数会让自动哈希失效）。
- **时间桶编进键**：跨期天然是新键，同桶重推被去重。dedup 键的 TTL 只做垃圾清理（默认 86400s，或 `expire_seconds*2`），不承担窗口语义。
- **失败自愈**：进 DLQ 的死任务只占住自己的时间桶（靠 TTL 过期），下一个时间桶是新键，调度下一轮自动恢复，不会被卡住。

各场景键模板与执行截止策略：

| 任务类型 | logical_key 示例 | 时间桶 | expire_seconds（执行截止） |
|---|---|---|---|
| 日 K 线 | `kline:AAPL:1d:2026-09-19` | 交易日 | 无或 7 天（回补合法） |
| 5min 行情快照 | `quote:AAPL:20260920T1310` | 采样周期 | 桶结束 + 容忍度（如 420s） |
| 股票列表发现 | `universe:20260920-am` | 固定窗口（如 4h） | 下个桶开始前 |
| 新闻抓取 | `news:sha256(url)` | 条目即身份 | 无或数天 |

三个生命周期不要混淆：dedup 键 TTL（垃圾清理）、`expire_seconds`（执行截止，pop 强制检查）、`ttl_days`（历史保留）。

同一 `logical_key` 建议同时作为数据库唯一约束（如 kline 表 symbol+period+trade_date）：队列挡投递、唯一键挡落库，两层一个身份。

## 使用约束（重要）

1. **at-least-once 交付**：crash recovery 会重跑"已执行但未 ack"的任务；handler 先推 result_queue 后 ack 的窗口也会造成下游重复。**落库逻辑必须幂等（upsert / 唯一约束）**。
2. **handler 必须自带超时**：库没有任务级超时，线程无法强杀。所有外部 HTTP 请求必须设置 timeout，否则线程永久占用并发额度，且 heartbeat 持续刷新导致 stale recovery 不会接管。
3. **重试语义**：`max_retry=3` 表示**总共执行 3 次**（首次 + 2 次重试），不是首次之外再重试 3 次。
4. **并发注意**：`max_workers>1` 时同一业务对象（如同一 symbol）的两个任务可能并发执行；需要严格串行请用 `max_workers=1` 或按对象分队列。库不做 per-key 串行化，logical_key 只保证"同键至多一个在途任务"。

## 定时抓取部署模式（场景示例）

定时抓取推荐**外置 cron + logical_key 去重**，而不是在 handler 里自我续期（失败进 DLQ 会断链，re-push 与 ack 之间的崩溃会分裂周期链）：

```python
# scheduler.py — 由 crontab / systemd timer 每 5 分钟调用
from qtask_list import SmartQueue

q = SmartQueue("redis://localhost:6379/0", "quote", namespace="stockev")

bucket = time.strftime("%Y%m%dT%H%M")  # 5min 时间桶编进身份键
for symbol in watchlist():
    q.push(
        {"action": "fetch_quote", "symbol": symbol},
        logical_key=f"quote:{symbol}:{bucket}",
        expire_seconds=420,  # 桶结束 + 容忍度，过期的快照不落地
    )
```

```bash
# crontab：每 5 分钟投一轮
*/5 * * * * cd /path/to/app && python scheduler.py
```

- 上一轮未消费完时，下一轮的同键任务被去重，不会堆积。
- 单只股票抓取失败进 DLQ 不影响下一轮（新桶新键），DLQ 只需定期巡检（`qtask status` / Dashboard）。
- 回补（批量下载）与定时任务**分队列部署**：单队列 FIFO 下大批量回补会让时效敏感的定时任务排队过期。

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

# 投递测试任务
qtask push stockev_list:fetch '{"action":"fetch_stock","symbol":"AAPL"}'
qtask push stockev_list:fetch --file task.json --delay 60

# 查看队列中的实际消息
qtask peek stockev_list:fetch --state ready -l 20
qtask peek stockev_list:fetch --state dlq --json

# 实时监控 (2 秒刷新)
qtask watch stockev_list:fetch -i 2

# 清空队列；需要同时删除历史时显式加 --include-history
qtask clear stockev_list:fetch --force
qtask clear stockev_list:fetch --include-history --force

# DLQ 重新入队
qtask requeue stockev_list:fetch --force
qtask requeue stockev_list:fetch --task-id <task_id> --force

# retry 队列 → 主队列
qtask retry stockev_list:fetch

# Crash recovery：默认只恢复 stale worker 的 processing，避免抢活跃 Worker 任务
qtask recover stockev_list:fetch
qtask recover stockev_list:fetch --force-active

# 查看历史
qtask history stockev_list:fetch
qtask history stockev_list:fetch -l 50
qtask history -t <task_id>

# 单任务查看、重放、删除
qtask task get <task_id>
qtask task requeue <task_id> --queue stockev_list:fetch --from dlq --force
qtask task delete <task_id> --queue stockev_list:fetch --force

# 启动用户代码中已注册 handler 的 Worker
qtask worker --module myapp.workers:worker

# 清理过期历史
qtask clean-history stockev_list:fetch -t 15
qtask clean-history

# 归档到 SQLite
qtask archive stockev_list:fetch -d 1

# Redis 内存监控
qtask monitor

# 启动 RemoteStorage 服务端（大 payload 外存）
qtask storage --port 8096 --data-dir ~/.qtask-storage --ttl-days 7

# 启动 Web Dashboard
qtask dashboard
```

Dashboard 是基于 React 的模块化控制台，启动后打开 `http://localhost:8765`。页面支持：

- 按队列查看 ready/processing/retry/dlq/delay/completed/failed/expired/history。
- 搜索 task_id、action、payload。
- 按创建时间和完成时间筛选任务。
- 查看任务详情和原始 JSON。
- 单任务重试、删除。
- 批量 drain retry、重放 DLQ、放回过期任务。
- 安全恢复 stale processing；强制恢复 active processing 需要显式确认。
- 投递测试任务，支持 delay 和 expire_seconds。
- 删除队列及关联历史记录。

远程查看时应启用登录，并显式监听远程地址：

```bash
qtask dashboard \
  --host 0.0.0.0 \
  --port 8765 \
  --user admin \
  --password '<strong-password>' \
  --no-open
```

也可以通过环境变量部署：

```bash
export QTASK_DASHBOARD_USER=admin
export QTASK_DASHBOARD_PASSWORD='<strong-password>'
export QTASK_DASHBOARD_SECRET='<random-secret>'
qtask dashboard --host 0.0.0.0 --no-open
```

设置 `QTASK_DASHBOARD_PASSWORD` 后，访问 `/` 会先跳转到 `/login`，所有 `/api/*` 管理接口也会校验登录会话。公网部署建议放在 HTTPS 反向代理后，并设置 `QTASK_DASHBOARD_SECURE_COOKIE=1` 或 CLI 参数 `--secure-cookie`。

## 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `redis_url` | `redis://localhost:6379/0` | Redis 连接地址 |
| `namespace` | `""` | 命名空间，多项目隔离 |
| `max_retry` | `3` | 最大执行次数（总共），超限进入 DLQ |
| `retry_backoff_base` | `30` | 重试退避基数（秒），指数退避；0 = 旧的立即重试 |
| `retry_backoff_max` | `3600` | 单次重试退避上限（秒） |
| `large_threshold` | `50KB` | 大 payload 阈值，超限走 RemoteStorage |
| `ttl_days` | `15` | 历史记录保留天数 |
| `record_history` | `True` | 是否写任务历史（高频低价值队列可关闭省内存） |
| `max_workers` | `1` | Worker 线程池并发数 |
| `maintenance_interval` | `1800` (30min) | 维护线程执行间隔（归档+健康检查） |
| `stale_recover_interval` | `300` | 维护线程接手失联 Worker 任务的间隔（秒） |
| `archive_dir` | `$QTASK_ARCHIVE_DIR` 或 `./archive_data` 绝对路径 | SQLite 归档目录；多 Worker 必须共享同一目录 |
| `monitor_threshold_mb` | `None` | Redis 内存告警阈值（MB），不设则不检查 |

## 环境变量

```bash
export REDIS_URL=redis://localhost:6379/0
export QTASK_DASHBOARD_USER=admin
export QTASK_DASHBOARD_PASSWORD=<strong-password>
export QTASK_DASHBOARD_SECRET=<random-secret>
export QTASK_DASHBOARD_SESSION_TTL=86400
export QTASK_DASHBOARD_SECURE_COOKIE=1
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
