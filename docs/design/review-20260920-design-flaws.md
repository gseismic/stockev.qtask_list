# qtask_list 设计 Review 报告

- 日期：2026-09-20
- Review 模型：GLM-5.3-Flash（builtin:bigmodel-coding-plan/GLM-5.3-Flash）
- Review 范围：`qtask_list/queue.py`、`worker.py`、`history.py`、`storage.py`、`archiver.py`、`admin.py`，以及 `examples/stockev/`、`examples/finance/` 和 README
- 目标场景（用户给定）：
  1. 下载股票数据（批量一次性下载）
  2. 定时抓取数据（可能一只股票一只股票地周期抓取）
  3. 抓取列表需要动态增加（如新闻源等）

---

## 总体结论

这套队列的**消费端设计是扎实的**：`BRPOPLPUSH` 可靠消费、per-worker processing + heartbeat 恢复、DLQ/重放、大 payload 外存、运维闭环（CLI/Dashboard），对"一只股票一个任务、单只失败不拖累全局"的任务粒度是合适的。

但站在目标场景看，**生产端/调度端存在三个关键设计缺口**：

1. 没有周期调度抽象（定时抓取只能靠脆弱的自我续期链）；
2. 没有任务去重/幂等投递（动态列表会重复投递）；
3. 重试没有退避（对网络抓取场景是硬伤）。

这三个缺口在"定时 + 逐只抓取 + 动态列表"场景下会实际踩坑，不是理论问题。

---

## 逐场景评估

### 场景 1：下载股票数据（批量一次性）

基本能用。`push_batch` 有 Redis Pipeline 优化，超 50KB 自动 zstd 压缩或走 RemoteStorage 外存，历史可追溯。

小问题：

- `push_batch`（`qtask_list/queue.py:113`）不支持 `delay_seconds` / `expire_seconds`，而单条 `push` 支持。批量下载想错峰投递只能循环单条 push，5000 只股票就是 5000 次网络往返。

### 场景 2：定时抓取（逐只股票周期抓取）—— 缺陷最集中的场景

#### P0：没有"周期任务"抽象，用户只能自建调度链，而自建链是脆的

库里只有一次性 `delay_seconds`（`qtask_list/queue.py:155`）。要实现"每天 18:00 抓全部股票"，现实的写法是 handler 末尾自我续期：

```python
@worker.on("fetch_stock")
def fetch(task):
    ...抓取...
    q.push(task, delay_seconds=86400)  # 排明天
    return result
```

这个模式有两个结构性问题：

1. **链会静默断掉**：任务重试 3 次耗尽进 DLQ 后，`push(明天)` 永远不会执行——这只股票从此从调度中消失，且没有任何告警（DLQ 不会自动重放）。抓取场景网络失败是常态，每只股票的调度链都是一颗随时会断的定时炸弹。
2. **调度生命周期和单次任务执行耦合**：正确的模型是"调度计划"（每只股票一条 cron/interval 记录）独立于"单次任务执行"存在，任务失败只影响这一轮，不影响下一轮的生成。当前库里没有地方存放"计划"。

#### P0：重试无退避，且 retry 队列约 2 秒就被搬空

`fail()` 把任务放 retry list（`qtask_list/queue.py:330`），而 `_poll_once()` 每次轮询都调 `move_retry()`（`qtask_list/worker.py:181`），队列连续有任务时轮询间隔远小于 2 秒。结果是：数据源 429 限频或网络抖动时，3 次重试在一两秒内全部烧完 → 直接进 DLQ，而数据源可能只是暂时不可用。对爬虫/行情 API 这几乎必然发生。

值得注意：delay ZSET + Lua 原子迁移这套基础设施已经在库里（`qtask_list/queue.py:356`），`fail()` 完全可以按 `_retry` 次数写入 delay（如 30s / 2min / 10min 指数退避），不需要新增架构。

#### P1：handler 没有超时机制

`handler(payload)` 直接同步调用（`qtask_list/worker.py:121`）。抓取代码里 requests 忘了设 timeout、或数据源 hang 住，线程就永久卡死；`max_workers=1` 时整个 worker 停摆，任务卡在 processing 只能手工 recover。对网络抓取这是基本需求。

注意：线程无法强杀，即使加超时标记 fail，僵尸线程仍占着并发额度。所以至少应在文档里把"handler 必须自带超时"写成硬约束，或提供带 timeout 的 future 监控。

#### P1：没有任何限速概念

`max_workers` 并发 + 零退避重试，对单一数据源的请求频率完全没有约束。如果"一只一只抓"的目的就是避免被封，目前只能在 handler 里 sleep（占着线程池名额）。库层面至少应明确限速是 handler 的责任并给出推荐模式（token bucket / 最小间隔）。

### 场景 3：动态增加抓取列表（新闻等）

加队列、加任务本身没问题——`push` 接受任意 payload，`queue_names()` 能自动发现新队列。缺陷在**重复投递没有防线**：

#### P0：没有去重/合并（dedup / coalescing）语义

`push()` 每次生成新 uuid（`qtask_list/queue.py:82`），同一只股票、同一个新闻 URL 推两次就是两次抓取。配合场景 2 问题更明显：如果上一轮还没抓完（数据源慢、积压），外部调度又推下一轮，同一股票会有两个待执行任务，越积越多。

动态列表场景的典型操作是"发现新股/新源 → 加入"，调度器重启、重复触发、人工误推都会造成重复。需要 `push(payload, dedup_key=...)`：用 `SET NX EX` 占位，ack 后释放，保证"同一 key 至多一个未完成任务"。

---

## 其他问题（按优先级）

### P1 – at-least-once 语义没有向用户声明

两处窗口：

- handler 结果先 push 到 result_queue 再 ack（`qtask_list/worker.py:123-126`），crash 后恢复会重复投递下游；
- 心跳过期 + 手工 recover 后原 worker 完成，ack 的 LREM 失败但副作用已发生（`qtask_list/worker.py:284-287` 只打 warning）。

这本身是合理的取舍，但用户必须知道"下游写入要幂等（upsert）"——README 完全没提，对股票入库这种场景会导致重复数据。

### P2 – 停止时可能卡住

`max_workers>1` 且信号量满时，主线程阻塞在 `_semaphore.acquire()`（`qtask_list/worker.py:194`），`stop()` 无法打断；且第二次 Ctrl+C 也无效（`stop()` 见 `running` 已 False 直接 return，`qtask_list/worker.py:265-270`）。建议 `acquire(timeout=...)` + 检查 shutdown_event。

### P2 – `push()` 的历史记录与入队非原子

`qtask_list/queue.py:101-106`：`history.record` 成功、`lpush` 失败会留下幽灵 pending 历史，出现在 expired 视图里。

### P2 – 队列发现靠全库 `SCAN *`

`qtask_list/admin.py:54` 对整个 DB `SCAN *` 并逐 key `TYPE` + `LINDEX`。目标场景会动态增队列（新闻源等），队列越多 dashboard 一次刷新越重。建议 push 时 `SADD qtask:queues` 维护注册表。

### P3 – 小问题清单

| 问题 | 位置 | 说明 |
|------|------|------|
| `move_delay` Lua 每次只搬 1 条 | `qtask_list/queue.py:363` | `while true` 单条循环，错峰后同一秒到期几千条时会阻塞 Redis；且每个 worker 每次 poll 都跑一遍 |
| expired 统计低效且不准 | `qtask_list/admin.py:122-144` | 逐条 `get_task` 最多 200 次往返，还做采样外推 |
| 归档目录是相对路径 | `qtask_list/archiver.py:52` | 默认 `archive_data/`，worker 的 CWD 决定归档落点，运维陷阱 |
| Monitor 阈值硬编码 | `qtask_list/worker.py:137` | 512MB 写死，注释说"可配置"但参数没暴露 |
| Redis 断连无退避 | `qtask_list/worker.py:218-219` | 断连时 worker 循环刷错误日志，无 backoff |

---

## 正面确认（当前设计做对了的）

- `BRPOPLPUSH` + per-worker processing + heartbeat 恢复：逐只股票的任务粒度天然适配，单只失败不拖累其他股票；
- 大 payload 外存 + zstd 压缩：新闻正文、K 线历史等大响应有出路；
- delay ZSET + Lua 原子迁移：错峰调度的基础已经在（虽然 retry 路径没用上它）；
- QueueAdmin / CLI / Dashboard 运维闭环：对"动态列表"的可观测性好；
- `recover` 默认只恢复 stale worker，避免抢占活跃 Worker（PLAN-010/013 的边界修正）。

---

## 建议优先级

1. **`fail()` 改走 delay ZSET 做指数退避**——改动最小（基础设施现成），直接解决场景 2 的重试硬伤；
2. **`push` 加 `dedup_key`**——场景 3 的前提保障；
3. **引入 Schedule 实体 + 调度物化**（哪怕先做成"独立的调度表 + worker 维护线程顺带生成任务"），让"每只股票一条计划"不再依赖单次任务的成功——场景 2 的根本解法；
4. **文档明确 at-least-once 语义 + handler 必须带超时/幂等**；
5. **`push_batch` 补 delay/expire 参数**。

> 后续如需实施，建议按 api-design-zh skill 的流程先出接口设计方案（含方案对比），再立 PLAN 执行。
