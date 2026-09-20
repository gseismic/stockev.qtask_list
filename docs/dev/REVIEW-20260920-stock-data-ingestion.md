# qtask_list 股票数据采集场景代码与设计 Review 报告

## 评审信息

- 评审日期：2026-09-20
- 评审模型：GPT-5（Codex）
- 评审范围：`qtask_list` 核心队列、Worker、任务历史、归档、RemoteStorage、QueueAdmin，以及股票 Pipeline 示例
- 使用场景：股票数据下载、定时抓取、按股票逐个抓取、动态增加新闻等任务
- 评审方式：代码阅读、项目历史文档阅读、现有测试执行、关键路径最小复现

## 总结结论

当前实现适合作为基于 Redis 的 at-least-once 任务执行队列，但还不适合直接作为完整的股票数据调度与采集系统。

队列的 ready、processing、retry、DLQ、delay、stale recovery 和基础运维能力已经形成，现有代码质量基线也较好；但股票采集场景还需要在队列之上补齐幂等、重试退避、调度、动态 fan-out、数据持久化和限流等业务能力。

最需要优先处理的是：

1. 明确并落实 at-least-once 下的业务幂等。
2. 为外部股票接口增加带退避的重试机制。
3. 修复自动归档 live task 历史的问题。
4. 增加动态任务扩散和父子任务关系。
5. 将定时调度与任务执行明确分层。

## 验证结果

当前验证结果：

- `python -m pytest -q`：94 passed
- `python -m ruff check .`：通过
- `python -m mypy qtask_list cli dashboard remote_storage`：通过

另外完成了关键路径最小复现：

- Handler 返回任务列表时，`result_queue` 会因为只接受字典而抛出 `'list' object has no attribute 'get'`，源任务进入 DLQ，子任务不会产生。
- 一个仍在 ready 队列中的旧 pending 任务被 `ArchiveManager` 归档后，Redis 历史记录被删除，但 ready 消息仍然存在。
- DLQ 任务重放时会保留原 `_retry` 值，人工重放后再次失败可能立即重新进入 DLQ。

## 详细问题

### R-001：必须按 at-least-once 语义实现业务幂等

严重度：高

任务消费、业务处理、下游投递和 ack 是分开的操作。Worker 先执行 Handler，再投递 `result_queue`，最后确认源任务；进程可能在这些步骤之间崩溃。

相关代码：

- [qtask_list/worker.py:120](/home/lsl/macbook/pai-studio-fin/projects/stockev.qtask_list/qtask_list/worker.py:120)
- [qtask_list/queue.py:282](/home/lsl/macbook/pai-studio-fin/projects/stockev.qtask_list/qtask_list/queue.py:282)

影响：

- 股票下载可能重复请求。
- 数据库写入可能重复。
- 动态生成的新闻子任务可能重复。
- 下游任务已经入队但父任务尚未 ack 时，父任务重试会再次生成下游任务。

建议：

- 为业务任务增加稳定的 `logical_key`，不要只依赖随机 `task_id`。
- 数据库使用唯一约束或 upsert。
- 子任务携带 `parent_task_id` 和 `trace_id`。
- 将“任务是否已产生”与“任务是否已执行”分开建模。

示例业务键：

```text
kline:provider:AAPL:1d:2026-09-19
news:provider:sha256(article_url)
```

### R-002：重试没有退避，会对外部股票接口形成重试风暴

严重度：高

`fail()` 会把任务立即放入 retry，Worker 下一轮又会把 retry 全部移动回 ready。

相关代码：

- [qtask_list/queue.py:303](/home/lsl/macbook/pai-studio-fin/projects/stockev.qtask_list/qtask_list/queue.py:303)
- [qtask_list/worker.py:179](/home/lsl/macbook/pai-studio-fin/projects/stockev.qtask_list/qtask_list/worker.py:179)

影响：

- API 限流或短暂故障时会快速重复请求。
- 多个 Worker 会放大重试压力。
- 当前没有区分临时错误和永久错误。
- 中间重试原因没有完整记录到历史中。

建议增加：

- 指数退避和随机抖动。
- `next_run_at` 或延迟 retry。
- 可重试错误分类。
- 最大总耗时。
- 明确 `max_retry` 是总执行次数还是额外重试次数。

当前代码在 `retry >= max_retry` 时进入 DLQ，因此 `max_retry=3` 实际最多执行 3 次，而不是初次执行后再重试 3 次，接口命名和文档需要统一。

### R-003：`expire_seconds` 只是历史标记，不是执行截止时间

严重度：高

`expire_seconds` 只写入 `expires_at`，Worker 不会在任务过期后阻止执行。

相关代码：[qtask_list/queue.py:97](/home/lsl/macbook/pai-studio-fin/projects/stockev.qtask_list/qtask_list/queue.py:97)

如果股票数据已经过时，当前系统仍可能继续抓取并写入结果。应明确区分：

- `deadline`：超过时间后禁止执行。
- `stale_after`：允许执行，但结果标记为过期。
- `retention_ttl`：历史保留时间。

如果业务只需要过期任务展示，建议将接口命名改得更明确；如果确实需要截止时间，则应在 Worker 执行前检查。

### R-004：自动归档可能删除仍处于 live 状态的任务历史

严重度：高

Worker 会周期性调用 `archive_to_sqlite(..., days_ago=1)`。归档逻辑按时间筛选历史，没有限制任务必须处于 completed、failed 或 DLQ 等 terminal 状态。

相关代码：

- [qtask_list/worker.py:161](/home/lsl/macbook/pai-studio-fin/projects/stockev.qtask_list/qtask_list/worker.py:161)
- [qtask_list/archiver.py:84](/home/lsl/macbook/pai-studio-fin/projects/stockev.qtask_list/qtask_list/archiver.py:84)

影响：

- 延迟超过一天的任务可能失去 Redis 历史。
- 长时间积压或长时间处理的任务完成后无法更新历史。
- Dashboard 只能看到队列消息，看不到完整生命周期。
- 多个 Worker 可能同时归档同一队列，并分别写入本地 SQLite。

建议：

- 只归档 terminal 状态任务。
- 归档前确认任务不在 ready、processing、retry、delay 中。
- 将归档作为独立维护服务，而不是每个 Worker 都执行。
- 生产环境使用集中式、可备份的归档存储。

### R-005：动态 fan-out 没有标准接口

严重度：高

`result_queue` 当前只支持 Handler 返回一个字典。新闻发现任务如果返回多个 URL，会在 `SmartQueue.push()` 中失败。

相关代码：

- [qtask_list/worker.py:123](/home/lsl/macbook/pai-studio-fin/projects/stockev.qtask_list/qtask_list/worker.py:123)
- [qtask_list/queue.py:71](/home/lsl/macbook/pai-studio-fin/projects/stockev.qtask_list/qtask_list/queue.py:71)

直接在 Handler 中调用 `push_batch()` 可以实现扩散，但父任务重试时会重复生成子任务，而且没有父子关系和原子提交语义。

建议增加显式的 fan-out 模型：

```text
discover task
  -> emit_many(child tasks)
  -> child tasks carry parent_task_id / trace_id / logical_key
  -> parent task ack
```

并明确父任务在“子任务全部成功入队”前是否允许 ack。

### R-006：当前系统没有真正的定时调度器

严重度：中高

`delay_seconds` 只支持相对当前投递时间的一次性延迟，不包含：

- 每日或每小时周期任务。
- 交易日历和节假日处理。
- 调度进程宕机后的补偿策略。
- 多实例调度锁。
- 同一业务窗口的去重。

建议采用分层架构：

```text
Scheduler
  -> 生成 symbol + dataset + 时间窗口任务
  -> qtask_list 负责排队、执行、重试
  -> 数据库 / 对象存储负责持久化结果
```

qtask_list 更适合做执行队列，而不是直接承担完整 Cron 或交易日历调度职责。

### R-007：历史记录不足以支持股票数据审计

严重度：中高

普通 `push()` 的历史主要保存 `action`、状态和时间，不保存 `symbol`、URL、数据周期、请求参数和结果引用。

相关代码：[qtask_list/queue.py:97](/home/lsl/macbook/pai-studio-fin/projects/stockev.qtask_list/qtask_list/queue.py:97)

任务完成后原始队列消息已被删除，因此无法通过 completed history 查询：

- 哪只股票。
- 哪个数据源。
- 哪个交易日或时间窗口。
- 使用了什么请求参数。
- 最终数据存在哪里。

建议将业务索引字段放入历史，完整数据保存到数据库或对象存储，并在任务历史中保存 `payload_ref`、`result_ref` 或数据版本。

### R-008：没有任务级 timeout，也没有按数据源或股票限流

严重度：中高

Heartbeat 是 Worker 级别的。如果抓取 HTTP 请求没有 timeout，任务可能永久停留在 processing；Worker 仍在刷新 heartbeat，因此 stale recovery 不会接管该任务。

相关代码：[qtask_list/worker.py:74](/home/lsl/macbook/pai-studio-fin/projects/stockev.qtask_list/qtask_list/worker.py:74)

`max_workers=1` 只能保证一个 Worker 实例内全局串行，不能保证多 Worker 实例下同一股票或同一 provider 不并发。

建议：

- 所有外部 HTTP 请求强制 timeout。
- 增加任务 deadline 或 watchdog。
- 增加 provider 级别 rate limiter。
- 需要时增加 symbol/provider keyed concurrency。

### R-009：人工重放不会自动重置重试次数，并可能抢占活跃任务

严重度：中高

DLQ 重放时原消息中的 `_retry` 会保留，任务再次失败时可能直接重新进入 DLQ。

相关代码：

- [qtask_list/queue.py:463](/home/lsl/macbook/pai-studio-fin/projects/stockev.qtask_list/qtask_list/queue.py:463)
- [qtask_list/admin.py:452](/home/lsl/macbook/pai-studio-fin/projects/stockev.qtask_list/qtask_list/admin.py:452)

同时，管理接口允许从 processing 移动任务到 ready。若 Worker 仍在执行，会导致重复执行或 active Worker 的 ack 失败。

建议：

- 默认禁止从 active processing 重放。
- 增加显式 `reset_attempts` 选项。
- 区分“继续原重试次数”和“人工全新重放”。

### R-010：RemoteStorage 生命周期和错误分类不适合长期任务

严重度：中

RemoteStorage 默认 TTL 为 7 天，而任务历史默认保留 15 天，队列任务还可能存在更久。

相关代码：[remote_storage/server.py:44](/home/lsl/macbook/pai-studio-fin/projects/stockev.qtask_list/remote_storage/server.py:44)

外存下载失败会在 pop 解码阶段被当作 poison message 直接放入 DLQ；但 HTTP 失败可能只是临时网络问题。

建议：

- 将 RemoteStorage 定位为临时任务 payload 缓存，而不是业务数据仓库。
- TTL 至少覆盖最大任务存活时间，或使用真正持久化的对象存储。
- 将外存读取失败区分为 transient error 和 permanent error。

### R-011：生产者投递和历史记录不是一个原子操作

严重度：中

`push()` 先写历史，再写 ready/delay 队列；两步之间发生 Redis 错误时可能出现历史中有任务但队列没有任务，或队列投递失败但业务侧已经认为任务创建成功。

相关代码：[qtask_list/queue.py:101](/home/lsl/macbook/pai-studio-fin/projects/stockev.qtask_list/qtask_list/queue.py:101)

建议使用统一的 Redis transaction/Lua 脚本，或者接受并设计一个可恢复的 producer outbox/reconciliation 机制。

## 对目标场景的适配性

| 场景 | 当前情况 | 判断 |
|---|---|---|
| 一只股票一个任务 | payload 粒度可以做到 | 支持 |
| 全局逐个抓取 | `max_workers=1` 可实现 | 仅适合单 Worker |
| 多股票并行、单股票不并发 | 没有 keyed concurrency | 需要补充 |
| 一次性延迟任务 | `delay_seconds` 支持 | 支持 |
| 每日/每小时定时抓取 | 没有周期调度器 | 需要外部 Scheduler |
| 动态增加新闻任务 | 可以直接 push | 需要 fan-out、幂等和父子关系 |
| 抓取结果持久化 | 不是 qtask_list 的职责 | 应接数据库/对象存储 |
| API 失败重试 | 有基础 retry | 必须补退避和错误分类 |

## 推荐目标任务模型

建议将随机任务 ID、业务幂等键、追踪信息和业务 payload 分开：

```json
{
  "action": "fetch_kline",
  "logical_key": "kline:provider:AAPL:1d:2026-09-19",
  "symbol": "AAPL",
  "dataset": "kline",
  "period": "2026-09-19",
  "trace_id": "...",
  "parent_task_id": "..."
}
```

`attempt`、`next_run_at`、错误分类等传输元数据不建议混入业务 payload；当前 `_retry`、`_compressed`、`_large` 混在 payload 中，容易造成接口语义和业务字段冲突。

## 建议实施顺序

### P0

1. 规定 at-least-once 语义，并在数据落库侧实现幂等。
2. 增加重试退避、错误分类和任务 deadline。
3. 修复归档逻辑，只处理 terminal task，不删除 live task 历史。

### P1

1. 增加 Scheduler 与调度去重。
2. 增加 fan-out、父子任务和 trace_id。
3. 扩充任务历史业务元数据和结果引用。
4. 增加 provider/symbol 级别限流与并发控制。

### P2

1. 统一 RemoteStorage 的 TTL 和错误处理。
2. 完善人工重放和 attempt reset 语义。
3. 增加任务延迟、重试、处理时长和数据新鲜度指标。
4. 对 `move_delay()` 和大批量动态投递增加批量上限，避免 Redis 单次脚本或 Pipeline 过大。

## 评审结论

建议保留 qtask_list 作为底层任务执行队列，但不要让它独自承担调度、幂等、业务数据存储和 provider 限流职责。

对于当前股票场景，最低可行方案是：外部 Scheduler + 每个 symbol/数据窗口一个任务 + 幂等落库 + 带退避的重试 + durable data store。新闻动态列表则应在此基础上增加显式 fan-out 和子任务去重。

