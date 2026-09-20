# qtask_list 整体设计 V2

- 日期：2026-09-20
- 状态：设计定稿，待分期实施
- 目标版本：`0.2.x`
- 适用范围：Python SDK、Worker、Redis 数据模型、RemoteStorage、历史归档、QueueAdmin、CLI、Dashboard
- 前置文档：
  - `docs/design/task-identity-20260920-logical-key.md`
  - `docs/design/qtask-design-review-20260920-scenarios.md`
  - `docs/design/review-20260920-design-flaws.md`
  - `docs/dev/REVIEW-20260920-stock-data-ingestion.md`
- 关联实施：`docs/dev/PLAN-014-review-fixes.md`

当本设计与上述旧设计在任务身份、去重生命周期、终态重放、历史保留或外存错误处理上冲突时，以本设计为准。

## 1. 摘要与核心裁决

qtask_list 的定位保持为：**可靠执行队列，而不是完整调度系统、交易日历、业务数据库或 exactly-once 平台**。

整体架构采用：

```text
外部 Scheduler / Discoverer / Reconciler
                │
                │ 生成带明确业务身份和计划窗口的 TaskSpec
                ▼
         qtask_list 执行队列
      ┌─────────┼──────────┐
      │         │          │
   去重与排队   重试与恢复   运维与审计
      │         │          │
      └─────────┼──────────┘
                ▼
       幂等业务存储 / 对象存储
```

本设计作出以下核心裁决：

1. `task_id` 表示一次具体执行实例；`logical_key` 表示一次期望的业务效果，二者不得混用。
2. “同一任务”不由 payload 字节相等决定，而由“完成其中一个是否会使另一个变得不再必要”决定。
3. 周期任务必须使用**计划发生时刻或数据窗口**构造身份，不能使用实际投递时刻。
4. 去重标记在任务 live 期间不得因普通 TTL 提前失效；TTL 只控制终态后的重复抑制保留期。
5. 自动重试沿用同一个 `task_id`；人工重放必须创建新 `task_id`，并记录 `replay_of`，终态不可重新打开。
6. deadline 明确定义为“最晚允许开始执行的时刻”，必须在解压和外存读取之前检查。
7. ready/processing/delay/retry/DLQ 等 operational location 以队列容器为事实来源；历史只保存必要运行元数据、审计信息和不可变终态，避免双份状态漂移。
8. ack、fail、skip、defer、DLQ、重放和历史结果必须通过统一的原子状态转换完成。
9. RemoteStorage 引用属于任务信封元数据；外存失败必须分类，并受 deadline、退避和最大尝试次数共同约束。
10. 永久业务去重由数据库唯一约束或持久化实体表兜底；Redis 去重不是业务数据的永久事实来源。

## 2. 目标与非目标

### 2.1 目标

本设计要可靠覆盖以下主要场景：

1. 大批量下载股票历史数据；
2. 按股票、数据集和数据分区拆分任务；
3. 每分钟、每 5 分钟、每天等周期抓取；
4. 早盘、午间、收盘等同日多次股票列表快照；
5. 新闻、公告、IPO 等动态增长列表的发现与 fan-out；
6. 数据源暂时不可用时带退避重试；
7. Worker 崩溃后的 at-least-once 恢复；
8. 大 payload 外存、外存过期和外存故障；
9. CLI、Dashboard、Agent 和 Python SDK 一致地投递和管理任务；
10. 任务历史清理、归档、DLQ 巡检和人工重放。

### 2.2 非目标

以下职责不进入 qtask_list 核心：

- cron、交易日历、节假日规则和补班规则；
- 股票数据、新闻正文和抓取结果的业务持久化；
- 跨 Redis 与外部数据库的 exactly-once；
- 自动推导业务身份；
- provider 级限流策略；
- 自动判断所有业务异常是否可重试；
- 永久保存所有任务 payload；
- 用一个队列同时解决所有优先级和资源隔离问题。

这些能力由 Scheduler、业务数据库、对象存储、provider 客户端和部署配置共同完成。

## 3. 代表性使用场景

### 3.1 历史数据分区下载

任务按稳定数据分区切分，例如：

```text
bars:tushare:SSE:600000:1d:qfq:2026-09-19:v1
```

身份必须包含所有会改变结果的维度：

- provider；
- market / exchange；
- symbol；
- dataset；
- interval；
- 复权方式、币种等变体；
- 数据时间窗口；
- 结果 schema 或算法版本。

今天和明天抓取同一个 `trade_date`，通常仍是同一逻辑任务。若要获取供应商修订数据，应显式提升 `revision` 或使用人工重放，而不是把抓取日期偷偷放进身份。

### 3.2 周期行情采样

任务身份使用 Scheduler 计划的采样时刻：

```text
quote:tushare:SSE:600000:5m:2026-09-20T09:35:00+08:00
```

09:35 与 09:40 是两个不同任务，即使 payload 其他字段完全相同。Scheduler 重启后补投 09:35 时，仍必须使用原计划时刻，而不是重启后的当前时间。

### 3.3 同日多次股票列表快照

若业务需要同时保留早盘与收盘快照：

```text
universe:tushare:SSE:2026-09-20T09:30:00+08:00
universe:tushare:SSE:2026-09-20T15:00:00+08:00
```

两者是不同逻辑任务。这样即使午后发生 IPO、停复牌或标的调整，晚间任务也不会被早间任务去重。

若业务只需要“当前最新列表”，则属于 latest-only 工作负载，应使用后文的 supersede 语义，而不是把所有快照错误地压成同一个 `logical_key`。

### 3.4 新闻动态发现与详情抓取

发现任务和条目任务是两个不同层级：

```text
发现任务：news-discover:reuters:2026-09-20T10:00:00Z
条目任务：news:reuters:article-12345:revision-1
```

发现任务按调度窗口或 cursor 区分；同一文章被多个发现轮次看到时，条目任务仍使用稳定 article ID 去重。只有 URL 时应先做业务侧规范化并哈希，避免访问跟踪参数破坏身份。

### 3.5 数据完整性修复

下一轮任务不能自动修复上一轮缺失的数据分区。例如 2026-09-19 日 K 线失败，2026-09-20 的成功不能补齐前一天。

因此业务侧需要 Reconciler：

1. 从业务数据库枚举缺失分区；
2. 使用原数据分区的 `logical_key` 检查仍保留的当前任务；长期完整性以业务数据库为准；
3. 对失败或缺失任务执行显式 replay；
4. 仍以数据库唯一约束保证落库幂等。

### 3.6 大 payload 任务

大 payload 可能在 ready、delay、processing 或 DLQ 中存活较长时间。外存对象的有效期必须覆盖任务的最长可操作生命周期，不能仅按固定 7 天清理而不考虑 deadline、重试和 DLQ 保留期。

## 4. 任务身份模型

### 4.1 核心概念

| 概念 | 用户含义 | 是否稳定 | 示例 |
|---|---|---|---|
| `task_id` | 一次具体执行实例 | 全局唯一 | UUID |
| `logical_key` | 一次期望的业务效果 | 由业务规则决定 | `bars:...:2026-09-19:v1` |
| `scheduled_for` | Scheduler 原计划发生时刻 | 对一次调度固定 | `2026-09-20T09:35+08:00` |
| `not_before_at` | 最早可开始时间 | 对实例固定 | 延迟执行时间 |
| `start_deadline_at` | 最晚可开始时间 | 对实例固定 | 桶结束 + 容忍度 |
| `attempt` | 自动执行尝试次数 | 同一实例递增 | 1、2、3 |
| `replay_of` | 本实例由哪个终态任务重放而来 | 创建后不变 | 旧 `task_id` |
| `trace_id` | 跨队列追踪一次业务流程 | 跨任务传播 | UUID |
| `parent_task_id` | fan-out 父任务 | 创建后不变 | discover task ID |

### 4.2 “同一任务”的判断规则

两个投递请求是同一逻辑任务，当且仅当：

> 完成其中任意一个，就会使另一个不再产生新的业务价值。

因此：

- payload 相同不代表任务相同；“抓 latest quote”今天和明天结果不同。
- 投递时间不同不代表任务不同；今天和明天回补同一个历史分区仍可能是同一任务。
- URL 不同不必然是不同新闻；跟踪参数不同可能指向同一文章。
- symbol 相同不代表任务相同；数据集、周期、provider、复权方式和窗口都可能不同。

### 4.3 键格式规范

推荐格式：

```text
{kind}:{provider}:{scope}:{subject}:{variant...}:{window-or-revision}
```

约束：

- 所有字段先做业务侧规范化；
- 时间统一使用带时区的 ISO 8601 或无歧义 UTC 表示；
- 不把 access token、签名参数等敏感信息写入键；
- 长 URL 等高基数字段使用稳定哈希；
- schema/算法变化会改变结果时必须进入键；
- 库限制明文 `logical_key` 的最大长度，并使用其哈希构造 Redis key；
- 明文值保存在任务记录中用于审计。

### 4.4 四类工作负载

| 类型 | 身份方式 | 每个窗口是否都要执行 | 推荐策略 |
|---|---|---:|---|
| 数据分区 | 数据集 + 精确窗口 + 变体 | 是 | stable logical key + 幂等落库 |
| 周期采样 | job + `scheduled_for` | 是 | 每个计划槽一个 key + deadline |
| latest-only 刷新 | subject + 最新版本 | 否 | 独立 supersede key |
| 实体/事件 | provider + entity ID + revision | 通常一次 | 业务数据库唯一键 + 队列防抖 |

`logical_key` 只解决幂等身份，不承担以下职责：

- 同一 symbol 串行执行：使用 `concurrency_key`；
- 新任务取代旧任务：使用 `supersede_key`；
- provider QPS：使用限流器；
- 高低优先级：优先使用分队列部署。

## 5. 方案比较与选择

### 5.1 方案 A：自动哈希 payload

优点：调用方最省事。

缺点：

- payload 中的当前时间、trace ID、URL 签名会制造假差异；
- “latest” 类任务 payload 相同但业务窗口不同；
- 两个语法不同的请求可能产生同一结果；
- 库无法理解 provider、复权和 revision 等业务含义。

结论：不采用。

### 5.2 方案 B：当前 `logical_key + 入队时 TTL`

优点：实现简单，适合短时间防抖。

缺点：

- TTL 到期时原任务可能仍在 ready、delay、processing 或 DLQ；
- 无 deadline 的任务默认一天后即可重复；
- “新闻条目永久同一”与默认一天 TTL 矛盾；
- SET 占位与入队之间存在崩溃窗口；
- 返回 `None` 无法告知被哪个任务去重。

结论：只可作为兼容模式，不再作为正式身份语义。

### 5.3 方案 C：语义身份 + live 锁 + 显式保留边界

规则：

1. enqueue 时原子创建任务、消息和 dedup owner；
2. 只要任务 live，dedup owner 就不因普通 TTL 消失；
3. 终态后根据明确的 `dedup_until` 保留或删除 owner；
4. 重复投递返回已有 `task_id` 和状态；
5. 永久业务唯一性仍由业务数据库负责。

优点：语义确定、可测试、可审计，并且覆盖长延迟和长积压。

代价：需要 V2 信封、原子脚本、迁移兼容和结构化返回值。

结论：采用方案 C。

## 6. 用户接口设计

### 6.1 分层 API

保留两层接口：

1. `push()`：常见场景的便捷兼容接口；
2. `enqueue(TaskSpec)`：表达完整任务语义的正式接口。

新代码推荐使用 `enqueue()`；旧 `push()` 内部转换为 `TaskSpec`。

### 6.2 TaskSpec

建议公共类型：

```python
@dataclass(frozen=True)
class TaskSpec:
    action: str
    payload: Mapping[str, JsonValue]
    logical_key: str | None = None
    scheduled_for: datetime | None = None
    not_before_at: datetime | None = None
    start_deadline_at: datetime | None = None
    dedup_until: datetime | None = None
    trace_id: str | None = None
    parent_task_id: str | None = None
    concurrency_key: str | None = None
    supersede_key: str | None = None
    supersede_version: int | str | None = None
```

时间参数必须是 timezone-aware `datetime`。内部统一转 UTC epoch。

字段语义：

- `action`：必填的处理器路由名，写入信封头；
- `logical_key=None`：不做业务去重；
- `dedup_until=None`：仅保证 live 期间同键唯一，终态后释放；
- `dedup_until=<绝对时刻>`：live 期间始终唯一，终态后至少保留到该时刻；
- `start_deadline_at`：超过后禁止开始，不代表 handler 运行超时；
- `scheduled_for`：审计和身份辅助字段，不自动产生 cron 行为；
- `concurrency_key`、`supersede_key` 为独立高级语义，不得用 `logical_key` 代替。

### 6.3 EnqueueResult

```python
@dataclass(frozen=True)
class EnqueueResult:
    accepted: bool
    task_id: str | None
    logical_key: str | None
    duplicate_of: str | None
    reason: Literal[
        "enqueued",
        "duplicate_active",
        "duplicate_retained",
        "superseded",
    ]
```

去重不再用模糊的 `None` 表示。Scheduler 可以记录 `duplicate_of`，Dashboard 也能跳转到原任务。

### 6.4 投递接口

```python
result = queue.enqueue(
    TaskSpec(
        action="fetch_quote",
        payload={
            "provider": "tushare",
            "symbol": "600000",
            "scheduled_for": slot.isoformat(),
        },
        logical_key=f"quote:tushare:SSE:600000:5m:{slot.isoformat()}",
        scheduled_for=slot,
        start_deadline_at=slot + timedelta(minutes=7),
        dedup_until=slot + timedelta(minutes=15),
    )
)
```

批量接口使用同一种元素类型：

```python
results = queue.enqueue_many([spec_a, spec_b, spec_c])
```

不再使用 `payloads`、`logical_keys` 等平行数组，避免位置错配，也允许每项拥有不同 deadline。

输入约束：

- `action` 非空且有长度上限；
- `logical_key` 为空字符串非法；提供 `dedup_until` 时必须同时提供 `logical_key`；
- `supersede_version` 与 `supersede_key` 必须成对出现；
- `logical_key` 明文默认不超过 512 UTF-8 bytes；
- `not_before_at` 不得晚于 `start_deadline_at`；
- 建议 `dedup_until >= start_deadline_at`，若更早则给出显式警告；
- 单次 `enqueue_many()` 默认最多 1000 项，超出由调用方或库内分块；
- payload 必须是可序列化 JSON object，序列化失败不得占用 identity。

### 6.5 重复处理策略

默认策略为拒绝重复并返回现有任务。危险覆盖使用具名枚举，不新增含糊 boolean：

```python
queue.enqueue(spec, on_duplicate=DuplicateAction.REJECT)
queue.enqueue(spec, on_duplicate=DuplicateAction.ALLOW_NEW)
```

`ALLOW_NEW` 必须：

- 创建新 `task_id`；
- 记录被覆盖的 dedup owner；
- 让新实例成为后续去重 owner；
- 在历史中标记 `duplicate_override_of`；
- CLI/Dashboard 要二次确认。

旧 `force=True` 映射到 `ALLOW_NEW`，并进入废弃流程。

### 6.6 人工重放

```python
result = admin.replay_task(
    failed_task_id,
    start_deadline_at=new_deadline,
)
```

重放规则：

- 原任务保持 `failed` 或 `skipped`，不改回 pending；
- 创建新 `task_id`；
- 新任务记录 `replay_of`；
- 默认继承 payload、logical key、trace 和 parent；
- 默认 attempt 从 0 开始；
- 若同 logical key 已有更新的 live owner，默认拒绝并返回冲突详情；
- 修改 payload、deadline 或身份必须在 API 中显式提供。

### 6.7 兼容便捷接口

```python
queue.push(
    payload,
    delay_seconds=0,
    expire_seconds=0,
    logical_key=None,
    dedup_ttl=None,
    force=False,
)
```

兼容映射：

- `delay_seconds` → `not_before_at=now+delay`；
- `expire_seconds` → `start_deadline_at=now+expire`；
- `dedup_ttl` → `dedup_until=now+ttl`，但 live owner 不会在该时刻被删除；
- `force=True` → `DuplicateAction.ALLOW_NEW`；
- `payload["action"]` → `TaskSpec.action`，兼容 handler 仍可收到原 payload 字段；
- 旧返回值保留一个小版本并发出 deprecation warning，随后统一为 `EnqueueResult`。

### 6.8 Handler 接口

V2 通过只读 `TaskContext` 暴露执行元数据，不再把 `_retry` 等字段混入 payload：

```python
@worker.on("fetch_quote")
def fetch_quote(payload: dict, context: TaskContext):
    logger.info(
        "task=%s logical=%s attempt=%s/%s",
        context.task_id,
        context.logical_key,
        context.attempt,
        context.max_attempts,
    )
    return fetch(payload)
```

`TaskContext` 至少提供：

- task_id、action、logical_key；
- attempt、max_attempts；
- scheduled_for、start_deadline_at；
- trace_id、parent_task_id、replay_of；
- worker_id；
- 只读取消/停止信号（供协作式超时与优雅停止）。

兼容期继续支持单参数 `handler(payload)`。Worker 在注册时检查函数签名，而不是在运行期用捕获 `TypeError` 猜测调用方式。新 handler 收到的 payload 与调用方提交的 payload 一致，不注入信封元数据。

## 7. 时间模型与调度边界

### 7.1 使用计划时刻，而不是运行时刻

Scheduler 必须先计算 `scheduled_for`，再构造 key：

```python
slot = floor_to_interval(now, minutes=5, timezone="Asia/Shanghai")
```

禁止直接使用 `time.strftime()` 的当前分钟冒充 5 分钟桶。Scheduler 补偿执行时仍使用原 slot。

### 7.2 三个时间概念

| 时间 | 含义 | 是否影响执行 |
|---|---|---:|
| `scheduled_for` | 业务原计划时刻 | 否，仅用于身份与审计 |
| `not_before_at` | 最早开始时刻 | 是 |
| `start_deadline_at` | 最晚开始时刻 | 是 |

`start_deadline_at` 不是 handler timeout。handler 的网络请求仍必须配置 timeout；未来任务 watchdog 是独立能力。

### 7.3 时钟规则

- 公共 API 只接受带时区时间；
- 内部使用 UTC epoch；
- 交易窗口由外部交易日历计算；
- 相对秒数仅作为便捷接口；
- 核心类注入 `Clock`，测试不依赖真实 sleep；
- 原子脚本需要当前时间时优先使用 Redis `TIME`，减少生产者与 Worker 时钟偏差。

### 7.4 外部调度器职责

Scheduler 负责：

- cron / systemd timer / Airflow 等周期触发；
- 市场时区和交易日历；
- 计算确定性 slot；
- 枚举 symbol；
- 产生 TaskSpec；
- 宕机后的调度补偿。

qtask_list 负责：

- 同一 slot 重复投递抑制；
- 排队、延迟、执行、退避和恢复；
- deadline 跳过；
- 审计和运维。

## 8. V2 任务信封

### 8.1 结构

```json
{
  "version": 2,
  "task_id": "uuid",
  "action": "fetch_quote",
  "logical_key": "quote:tushare:SSE:600000:5m:...",
  "attempt": 0,
  "created_at": 1789870000.0,
  "scheduled_for": 1789870500.0,
  "not_before_at": 1789870500.0,
  "available_at": 1789870500.0,
  "delay_reason": "schedule",
  "start_deadline_at": 1789870920.0,
  "dedup_until": 1789871400.0,
  "trace_id": "uuid",
  "parent_task_id": null,
  "replay_of": null,
  "payload": {
    "kind": "inline",
    "data": {
      "symbol": "600000"
    }
  }
}
```

`payload.kind`：

- `inline`：直接 JSON；
- `zstd`：压缩后的 base64 数据；
- `external`：RemoteStorage 引用。

外存引用格式：

```json
{
  "kind": "external",
  "key": "content-key",
  "size": 123456,
  "sha256": "...",
  "retain_until": 1790470000.0
}
```

### 8.2 信封不变式

- `_retry`、`_large`、`_compressed` 不再写入业务 payload；
- action 位于信封头，因此路由、诊断和未知 action 检查不依赖 payload 解压或外存；
- handler 永远只看到原始业务 payload；
- attempt、deadline、logical key、trace 和存储引用属于信封；
- 任意压缩或外存形式都必须保留相同元数据；
- 重试只修改 attempt 和错误元数据，不重新生成身份；
- 信封必须有版本号，V1 解码器在迁移期保留。

### 8.3 pop 处理顺序

严格顺序：

```text
BRPOPLPUSH 到 processing
        │
        ▼
仅解析 V2 信封头
        │
        ▼
原子 begin_attempt
        ├─ 重新检查 start_deadline，已过 ──> skip，不消耗 attempt
        ├─ 检查 supersede_version，已旧 ──> cancel，不消耗 attempt
        └─ 校验 attempt budget 后 attempt+1
        │
        ▼
解压或读取 RemoteStorage
        │
        ├─ 永久错误 ──> 原子 fail / DLQ
        │
        ├─ 瞬时错误 ──> 记录当前 attempt，按退避进入 delay
        │
        ▼
交给 handler
```

这保证已过期的大 payload 不会先产生网络请求，也保证外存异常不能绕过最大尝试次数。

## 9. 状态模型

### 9.1 两个正交维度

避免把队列位置和生命周期结果混在一个 `status` 字段中。

**队列位置（location）**由 Redis 容器直接决定：

- ready；
- processing；
- retry_wait / delay；
- DLQ；
- none。

**生命周期结果（outcome）**保存在任务记录中：

- none；
- completed；
- failed；
- skipped；
- cancelled。

`expired` 不是状态，而是派生条件：

```text
outcome == none
AND location in {ready, delay, retry_wait}
AND now > start_deadline_at
```

任务被 Worker 处理后，过期任务转为 `skipped`，不再出现在 expired 视图。

### 9.2 状态转换

```text
enqueue
  ├─ not_before_at 在未来 ──> delay
  └─ 可立即执行 ───────────> ready

ready ──claim──> processing

processing
  ├─ deadline missed ───────> skipped + location none
  ├─ superseded ────────────> cancelled + location none
  ├─ success ───────────────> completed + location none
  ├─ retryable failure
  │    ├─ attempts remain ──> retry_wait/delay
  │    └─ exhausted ────────> failed + DLQ
  └─ permanent failure ─────> failed + DLQ

DLQ
  ├─ replay ────────────────> 原任务保持 failed；创建新 task_id
  └─ purge ─────────────────> 原任务保持 failed；location 变 none
```

### 9.3 终态不可变

`completed`、`failed`、`skipped`、`cancelled` 一旦写入，不再改回 pending。这样才能保证：

- 归档记录可信；
- 失败历史不会被重放覆盖；
- attempt 与人工 replay 可以区分；
- clean_history 不会删除随后又要更新的记录；
- 同一逻辑任务的多次人工运行可通过 lineage 审计。

### 9.4 DLQ 的定义

DLQ 是失败任务消息的运维保留位置，不是另一种 outcome。一个任务可以同时满足：

```text
outcome = failed
location = DLQ
```

只要 DLQ 消息仍可重放，任务记录和外存引用就不得被普通历史清理删除。

## 10. Redis 数据模型与原子性

### 10.1 Key 结构

沿用每队列多容器模型，并新增正式注册表：

```text
{base}                              ready List
{base}:processing:{worker_id}       processing List
{base}:delay                        delay/retry ZSET
{base}:dlq                          DLQ List
{base}:worker:{worker_id}           heartbeat String
{base}:dedup:{logical_key_hash}     dedup owner Hash/String
qtask:task:{task_id}                task audit Hash
qtask:hist:{base}                   history ZSET
```

`retry` List 只保留兼容读取；V2 的自动重试统一进入 delay ZSET。

### 10.2 Dedup owner

dedup owner 至少保存：

```json
{
  "task_id": "uuid",
  "logical_key": "明文",
  "generation": 3,
  "outcome": "none",
  "dedup_until": 1789871400.0
}
```

规则：

- outcome 为 none 时不设置普通过期 TTL；
- 任务进入终态后，若 `dedup_until` 在未来，则设置绝对过期；
- `dedup_until` 已过或未提供时删除 owner；
- 删除、重放和覆盖使用 compare-and-delete / compare-and-set，不能误删新 owner；
- 队列清空和删除必须明确处理 dedup registry；
- `delete_queue` 必须删除所有 dedup owner；
- `clear_queue` 提供 `identity_policy=KEEP|RELEASE`，默认 KEEP 并清楚提示。

`clear_queue(identity_policy=KEEP)` 不能直接删除消息后留下永久 live owner。它必须分批把被清理任务转为 `cancelled`，再按各自 `dedup_until` 保留 owner；`RELEASE` 则在取消任务后显式释放 owner。`delete_queue` 才是删除消息、历史和 identity registry 的彻底销毁操作。

### 10.3 原子脚本

定义少量内部状态转换原语，所有 SDK、Admin、CLI 和 Dashboard 共用：

1. `enqueue_v2`：检查/占用 dedup、写任务记录、写历史索引、入 ready/delay；
2. `begin_attempt`：确认 processing 所有权，使用 Redis 时间原子检查 deadline/supersede/attempt budget；可执行时 attempt+1、替换 processing 中的信封并记录 started_at，否则直接 skip/cancel/fail；
3. `complete_task`：确认 processing 所有权、删除消息、写 completed、处理 dedup owner；
4. `retry_task`：删除 processing、记录本次失败、写 delay 和下一次 available_at；
5. `fail_task`：删除 processing、写 DLQ、写 failed、处理 dedup owner；
6. `skip_task`：删除 processing、写 skipped、处理 dedup owner；
7. `cancel_task`：删除 operational message、写 cancelled；
8. `replay_task`：消费原 DLQ 消息并原子创建新任务实例；
9. `admin_move`：有条件地移动 operational message，不在 moved=0 时修改历史；
10. `purge_task`：删除运维消息、更新可归档条件、按策略释放身份。

脚本必须：

- 验证消息仍属于预期 processing/list/ZSET；
- 验证 task_id 和必要的 generation；
- moved=0 时完全不产生其他写入；
- 可重复调用而不会产生第二条消息；
- 返回结构化结果码，不依赖日志字符串判断。

### 10.4 BRPOPLPUSH 边界

ready → processing 继续由 `BRPOPLPUSH` 保证原子可靠消费。任务历史不复制 live location；Dashboard 从容器读取 location，因此这一步无需再写一份可能漂移的 processing 状态。

解析信封头后调用 `begin_attempt`，由脚本重新检查 deadline 与 supersede，再原子递增 attempt、更新 processing 中的消息并记录 started_at。若 `begin_attempt` 未完成，原消息仍留在 processing，stale recovery 可安全恢复；任务归属始终以 processing 容器为准。

当前目标明确支持 standalone Redis 和 Sentinel 主从部署，不宣称支持 Redis Cluster。未来若支持 Cluster，参与同一 Lua 脚本的 key 必须使用同一 `{base}` hash tag，并相应迁移 task/history key。

### 10.5 生产者原子边界

RemoteStorage 上传发生在 Redis enqueue 之前，不可能与 Redis 做单一事务。处理方式：

1. 先准备 payload 或外存引用；
2. 执行 `enqueue_v2` 原子脚本；
3. enqueue 明确失败时对新建外存对象做 best-effort 清理；
4. 网络结果不确定时按 task_id 查询 enqueue 结果，不盲目释放 dedup owner；
5. 后台 orphan cleaner 清理没有任务引用的外存对象。

对大 payload 可先做一次只读 dedup preflight，减少明显重复任务的上传；最终是否接受仍以 `enqueue_v2` 原子脚本为准。并发竞态中落败的上传由内容寻址或 orphan cleaner 回收。

### 10.6 内部一致性检查

Maintenance 提供低频、可限速的内部一致性诊断，检查：

- dedup owner 指向不存在的 task；
- outcome 为 none 但不存在任何 operational message；
- terminal task 的 operational message 标记与实际容器不一致；
- history index 指向不存在的 task hash；
- processing key 无 heartbeat；
- 外存引用已接近 retain_until 但任务仍可操作。

自动修复必须使用相同的条件状态转换脚本；无法确定所有权时只报告，不猜测性删除。该检查只维护队列内部一致性，不替代业务数据 Reconciler。

## 11. 重试与错误模型

### 11.1 命名

新 API 使用 `max_attempts`，表示总尝试次数：

```text
max_attempts = 首次尝试 + 自动重试次数
```

旧 `max_retry` 在兼容期映射到相同语义并标记废弃，避免名称继续误导。

### 11.2 attempt 计数

任务在 deadline/supersede 检查通过后执行一次 `begin_attempt`，此时 attempt 增加一次。随后发生的任何可重试失败都消耗本次 attempt，包括：

- RemoteStorage 瞬时不可用；
- 解压或下载的可恢复错误；
- handler 可重试异常；
- result emission 的可重试失败。

永久错误不做无意义的自动重试，可直接进入 DLQ。

enqueue 时 `attempt=0`；第一次实际尝试为 1。当 `attempt == max_attempts` 的尝试失败后进入 DLQ，不再产生下一次尝试。deadline 或 supersede 在 `begin_attempt` 前命中，不消耗 attempt。

### 11.3 错误分类

内部错误码至少包括：

| 类型 | 示例 | 默认处理 |
|---|---|---|
| transient | timeout、连接失败、外存 5xx | 记录当前 attempt，退避 |
| throttled | provider 429、显式限流 | 按 retry-after 或退避 |
| permanent_payload | 外存 404、校验失败、非法 JSON | 直接 failed/DLQ |
| configuration | 缺少 storage、未知 action | 直接 failed/DLQ + 告警 |
| business_permanent | 非法 symbol、权限永久拒绝 | 直接 failed/DLQ |
| deadline | 开始截止已过 | skipped，不进 DLQ |
| superseded | 已有更新版本 | cancelled，不进 DLQ |

公共异常建议：

```python
RetryableTaskError(code, message, retry_after=None)
PermanentTaskError(code, message)
```

未分类 handler 异常保持向后兼容：默认可重试，直到 `max_attempts`。

### 11.4 退避

```text
delay = min(base * 2^(attempt-1), max_delay) + jitter
```

规则：

- deadline 优先于剩余 attempt；
- 若下一次 `run_at >= start_deadline_at`，直接 skipped；
- `retry_after` 存在时取其与最小退避的较大值；
- 所有 retry 都进入统一 delay ZSET；
- `available_at` 表示本次可再次领取的时间，`delay_reason` 区分初始 schedule 与 retry；
- 最后错误码、原因、时间和 attempt 写入历史。

## 12. RemoteStorage 生命周期

### 12.1 定位

RemoteStorage 是任务 payload 存储，不是股票数据或新闻数据仓库。

### 12.2 最小契约

客户端需要区分：

- 404 / object expired；
- 401/403 配置或权限错误；
- 408/429/5xx；
- 网络连接错误；
- checksum mismatch；
- JSON decode failure。

不能再把所有异常统一包装成 transient。

### 12.3 保留期

外存必须满足：

```text
retain_until >= max(
    start_deadline_at,
    最大自动重试结束时刻,
    DLQ 可重放保留时刻
) + safety_margin
```

若任务没有有界 deadline 且 DLQ 可无限保留，则必须使用持久存储，不能使用固定 7 天临时 TTL。

内容寻址对象可能被多个任务共享，因此不能在单个任务 ack 后直接删除。可选实现：

- 服务端维护引用计数；或
- 任务创建时延长对象 `retain_until`，后台按最大保留时刻清理；或
- 使用真正持久的对象存储生命周期策略。

第一阶段优先采用 `retain_until` 延长策略，避免引入分布式引用计数。

## 13. 历史、DLQ 与归档

### 13.1 历史记录字段

任务记录至少保存：

- task_id、logical_key、queue、action；
- trace_id、parent_task_id、replay_of；
- created_at、scheduled_for、started_at、finished_at；
- start_deadline_at、dedup_until；
- attempt、max_attempts；
- outcome、reason_code、reason；
- payload kind、payload_ref、payload checksum；
- result_ref（若业务选择记录）；
- replayed_by / duplicate_override_of；
- 是否仍有 operational message。

历史配置改为分级模式：

- `full`：保存完整审计字段和 attempt 事件，适合关键任务；
- `minimal`：只保存状态机正确性、身份、时间和最后错误所需字段，适合高频任务；
- 不提供会破坏状态机的完全关闭模式。

旧 `record_history=False` 映射为 `minimal`，而不是完全不创建任务记录。可选的详细 payload/result 审计可以关闭，但 correctness metadata 必须存在。

### 13.2 保留规则

1. outcome 为 none 的任务记录不设置历史 TTL；
2. failed 且仍在 DLQ 的任务记录不清理；
3. 只有终态且不再有 operational message 的记录，才从 `finished_at` 开始计算 TTL；
4. clean_history 只处理满足第 3 条的记录；
5. Redis key 自身的 expire 与 clean_history 使用同一终态规则；
6. 历史索引不能因队列一段时间没有新任务而整体提前过期。

### 13.3 归档规则

归档仅处理：

```text
outcome in {completed, failed, skipped, cancelled}
AND operational_message == false
AND finished_at < cutoff
```

归档写入成功后才能删除 Redis 记录。多 Worker 不再各自无锁归档：

- 推荐独立 maintenance 进程；或
- 使用 Redis leader lock，只允许一个实例归档同一队列；
- SQLite 路径必须共享且可备份；
- Dashboard 后续应支持读取归档，或明确归档后只保留离线查询。

### 13.4 人工 replay 与归档

replay 消费原 DLQ 消息、创建新 task_id，并在原记录写 `replayed_by`。原记录随后可按终态保留规则归档，新实例拥有独立生命周期。

## 14. Worker 设计

### 14.1 执行流程

```text
获取本地并发许可
  → 刷新 heartbeat
  → move_due_delay
  → BRPOPLPUSH
  → 校验信封/deadline/supersede
  → begin_attempt
  → 读取 payload
  → 提交 handler
  → complete/retry/fail 原子转换
```

### 14.2 Heartbeat 与恢复

- 每个 Worker 使用独立 processing key；
- heartbeat 刷新不得与归档、监控等慢操作共用同一阻塞循环；
- stale recovery 只恢复 heartbeat 已失效的 processing；
- recovery 只改变 location，不改变 task_id、attempt 或 outcome；
- 正在优雅 drain 时持续 heartbeat；
- 强制恢复活跃 Worker 属于危险操作，CLI/Dashboard 必须确认。

### 14.3 并发与 keyed concurrency

V2 基线仍允许不同任务并发。若业务要求“同一 symbol 串行、不同 symbol 并行”，使用独立 `concurrency_key`：

```text
symbol:tushare:SSE:600000
```

实现要求：

- 分布式 lease；
- lease token 防止旧持有者误释放新锁；
- handler 长跑时续租；
- 获取失败时进入短 delay，不消耗业务 attempt；
- Worker 崩溃后 lease 自动过期。

该能力不与 logical key 合并，安排在 V2 基线正确性之后实施。

### 14.4 latest-only / supersede

对于只关心最新结果的刷新任务，`supersede_key` 维护最新版本。Worker 在读取外存前比较版本：

- 当前任务版本小于最新版本：cancelled，reason=`superseded`；
- 等于最新版本：正常执行；
- 不扫描并主动删除所有旧消息，避免高成本队列遍历。

若业务需要保存早晚两个快照，则不要启用 supersede。

### 14.5 Handler 超时与幂等

- 库不承诺强杀 Python 线程；
- HTTP、数据库和对象存储请求必须设置自己的 timeout；
- handler 必须按 logical key 或业务唯一键幂等；
- 外部副作用成功、ack 前崩溃仍可能重复执行；
- 对强一致下游使用业务 outbox / inbox，而不是依赖队列宣称 exactly-once。

## 15. Fan-out 与多级流水线

### 15.1 动态 fan-out

新闻 discover handler 推荐：

1. 为每个条目构建 `TaskSpec`；
2. 子任务带 stable logical key；
3. 使用 `enqueue_many()`；
4. 子任务记录 `trace_id` 与 `parent_task_id`；
5. 全部 enqueue 成功或明确被去重后，父任务才能 ack。

父任务因 crash 重跑时，子任务身份保证 fan-out 幂等。

### 15.2 result_queue

自动下游投递不能只接受裸 dict。新接口允许 handler 返回：

```python
TaskResult(
    value=result,
    emissions=[TaskSpec(...), TaskSpec(...)],
)
```

兼容期内返回 dict 仍投递一个下游任务，但文档明确它不具备自动 logical key。需要可靠身份的流水线必须返回 TaskSpec 或在 handler 中显式 enqueue。

跨队列下游投递与当前任务 ack 若位于同一 Redis，可进一步使用原子脚本；涉及外部系统时仍需 outbox。

## 16. Admin、CLI 与 Dashboard

### 16.1 单一业务入口

QueueAdmin 不再自行重建部分 SmartQueue 配置。构造时完整注入：

- Redis client；
- RemoteStorage；
- 默认 max_attempts / backoff；
- clock；
- envelope codec；
- history/retention 配置。

所有管理入口调用同一个 `enqueue(TaskSpec)` 和状态转换层。

### 16.2 投递能力对齐

Python、Admin、CLI、Dashboard 必须同时支持：

- logical key；
- scheduled_for；
- not_before / delay；
- absolute start deadline / relative expire；
- dedup_until；
- duplicate action；
- trace / parent；
- RemoteStorage；
- 结构化 EnqueueResult。

Dashboard 将低频参数放在“高级选项”，保持简单路径简短。

### 16.3 状态视图

用户可见：

- ready、processing、retry_wait、delay、DLQ；
- completed、failed、skipped、cancelled；
- deadline_missed（派生筛选，不是终态）；
- duplicate/superseded 统计。

旧 `expired` 名称兼容一个版本，文档改为 `deadline_missed`，避免与历史 TTL 混淆。

### 16.4 危险操作

以下操作必须显式确认并返回审计结果：

- 允许同 logical key 创建并发新实例；
- 强制恢复活跃 Worker；
- 清空队列并释放 identity；
- 删除队列及 dedup registry；
- 从 DLQ 批量 replay；
- 延长或清除 deadline；
- purge DLQ payload。

“重放过期任务”不再仅修改历史字段；必须创建新实例并给出新 deadline，避免消息信封仍保留旧 deadline。

## 17. 可观测性与告警

### 17.1 指标

至少输出：

- enqueue accepted / duplicate_active / duplicate_retained；
- ready、processing、delay、DLQ 深度；
- queue wait latency、handler duration；
- attempts 分布、retry 原因；
- deadline_missed / skipped；
- payload transient / permanent / checksum failure；
- stale recovery 数量；
- DLQ oldest age；
- replay 数量与成功率；
- superseded/cancelled 数量；
- history/archive failure。

### 17.2 告警

最小生产告警：

- DLQ 数量或最老年龄超阈值；
- 连续 payload 404；
- Worker heartbeat 全部消失但 ready 有积压；
- deadline_missed 比例异常；
- 同 provider 大量 throttled；
- history/archive 连续失败；
- Scheduler slot 缺口和业务数据分区缺口。

最后两项需要 Scheduler/Reconciler 或业务数据库配合，不能只靠队列深度判断。

## 18. 部署模型

### 18.1 分队列隔离

推荐至少拆分：

```text
stockev:backfill:*    历史回补，吞吐优先
stockev:realtime:*    实时行情，时效优先
stockev:discover:*    列表/新闻发现
stockev:fetch:*       详情抓取
stockev:store:*       幂等落库
```

单队列 FIFO 下不要把大量回补和时效敏感任务混合。优先级先通过分队列和独立 Worker 配额解决。

### 18.2 provider 限流

provider 限流位于业务客户端或共享 limiter：

- provider 维度 token bucket；
- 识别 Retry-After；
- 限流等待不应烧掉大量自动 attempt；
- 可按 provider 拆队列或 concurrency group。

### 18.3 调度与完整性

推荐生产组件：

```text
Scheduler：产生计划任务
Worker：执行任务
Reconciler：检查缺失业务分区并 replay
Maintenance：归档、清理、DLQ 告警
Business DB：结果唯一约束与完整性事实来源
```

## 19. 兼容与迁移

### 19.1 版本策略

本设计包含公开接口和行为变化，应发布为至少 `0.2.0`。

### 19.2 双格式读取

- Worker 同时读取 V1 和 V2 信封；
- 新任务默认写 V2；
- V1 `_retry`、`_large`、`_compressed` 在 decode 时转换成内部 V2 模型；
- V1 缺少 logical key、deadline 等字段时不凭空推断；
- 所有 V1 队列清空或自然耗尽后移除旧写路径。

### 19.3 去重迁移

- 识别现有 string dedup key；
- 命中旧 key 时仍返回 duplicate，不直接覆盖；
- 新任务写 versioned owner；
- 后台迁移或 TTL 自然淘汰旧 key；
- clear/delete 工具显示旧、新 dedup key 处理数量。

### 19.4 API 迁移

| 旧接口 | 新接口 | 策略 |
|---|---|---|
| `push()` | `enqueue(TaskSpec)` | 保留便捷适配 |
| `push_batch(payloads, logical_keys=...)` | `enqueue_many(specs)` | 旧接口废弃提醒 |
| `max_retry` | `max_attempts` | 兼容映射 |
| `expire_seconds` | `start_deadline_at` | 保留相对时间便捷参数 |
| `dedup_ttl` | `dedup_until` | live 期间不再到期 |
| `force=True` | `DuplicateAction.ALLOW_NEW` | 危险操作显式化 |
| 同 task_id DLQ requeue | 新 task_id replay | 行为变更 |
| `expired` 状态 | `deadline_missed` 筛选 | 兼容别名 |

## 20. 分期实施建议

### 阶段 A：立即正确性修复

1. deadline 检查移到外存读取前；
2. 外存 404/配置错误/瞬时错误分类；
3. 外存瞬时失败消耗 attempt，并受 max_attempts/deadline 约束；
4. clean_history 不删除任何仍有 operational message 的记录；
5. 修复 skipped 被统计为 expired；
6. QueueAdmin 完整透传 storage 与 logical key 参数；
7. admin moved=0 时不更新历史；
8. 修复过期 ready 任务只清 history、不改信封的问题；
9. 更新失效的手动验证脚本并恢复 ruff 全绿。

### 阶段 B：V2 信封与原子状态机

1. 引入 TaskSpec、EnqueueResult 和 V2 envelope codec；
2. 元数据移出业务 payload；
3. 实现 enqueue/complete/retry/fail/skip 原子脚本；
4. 实现 state-aware dedup owner；
5. live 历史不设 TTL，终态后开始保留期；
6. 统一 max_attempts；
7. 新 task_id replay 和 lineage；
8. 双格式兼容与迁移测试。

### 阶段 C：入口与业务示例

1. Admin、CLI、Dashboard 对齐完整投递参数；
2. 重写 `examples/stockev/`；
3. 提供确定性 5 分钟 slot helper 示例；
4. 提供历史分区、早晚 universe、新闻 discover/fan-out 示例；
5. 提供 Reconciler 示例；
6. 增加 DLQ 告警和关键指标。

### 阶段 D：高级执行控制

1. keyed concurrency；
2. supersede/latest-only；
3. provider limiter 集成点；
4. TaskResult/emissions；
5. 独立 maintenance leader；
6. archive 查询闭环。

## 21. 验收标准

### 21.1 身份与时间

- 同一计划 slot 重复投递返回 `duplicate_of`；
- 下一 slot 正常创建新任务；
- 调度迟到仍使用原 scheduled_for 和绝对 deadline；
- 同一历史数据分区跨天回补保持同 logical key；
- 早盘、晚盘 universe 是不同 key；
- 新闻相同 article ID 在保留期内只创建一个实例。
- V2 TaskSpec 的 action 在信封头中可见，外存不可用时仍能正确诊断路由。

### 21.2 去重与并发

- dedup_until 已过但原任务仍 live 时，仍拒绝重复；
- 终态后按 dedup_until 精确释放；
- enqueue 崩溃不能留下“有 owner、无任务”的孤儿状态；
- ALLOW_NEW 后新实例成为 owner；
- clear/delete 的 identity 行为可选择且可审计。

### 21.3 deadline 与外存

- 过期 external payload 不发起下载；
- 404 直接进入 failed/DLQ；
- timeout/5xx 按退避且 attempt 递增；
- 瞬时故障不能超过 max_attempts；
- 下一次 retry 已越过 deadline 时直接 skipped；
- compressed/external/inline handler 看到完全一致的业务 payload。
- 首次实际执行 attempt=1，deadline/supersede 在开始前命中不消耗 attempt。

### 21.4 历史与重放

- ready、processing、delay、retry、DLQ 任一位置存在时，历史不被 clean/expire；
- ack/fail 后队列位置和 outcome 不会部分成功；
- moved=0 不改变历史；
- DLQ replay 创建新 task_id，原失败记录不变；
- archive 只处理终态且无 operational message 的记录。

### 21.5 入口一致性

- 同一 TaskSpec 从 Python、Admin、CLI、Dashboard 投递得到相同行为；
- Dashboard 配置的 RemoteStorage 实际用于 push 和 payload resolve；
- CLI 支持 logical key、deadline、dedup_until 和结构化 JSON 输出；
- 危险覆盖和 identity 清理必须显式确认。

### 21.6 工程质量

- pytest、ruff、mypy 全绿；
- 新状态转换有并发和故障注入测试；
- 手动端到端脚本不依赖旧的立即重试假设；
- 文档、examples、skill 与实际 API 同步；
- 版本号和行为变更说明完整。

## 22. 三轮查漏结果

### 第一轮：业务语义检查

检查问题：批量历史、周期采样、早晚列表、动态新闻和数据修复是否能被同一身份模型表达。

结论：仅靠“任务类型 + 主体 + 时间桶”仍不够，必须加入 provider、变体、明确数据窗口和 schema/revision；发现任务与条目任务必须分层；latest-only 不能误用 logical key。

### 第二轮：故障与并发检查

检查问题：崩溃窗口、长时间积压、外存丢失、history TTL、DLQ 重放、Redis 部分失败是否破坏不变式。

结论：当前入队 TTL 去重、可重新打开的终态、历史创建时 TTL 和非原子状态转换均不可保留；必须使用 live owner、终态不可变、新 task_id replay、V2 信封和原子脚本。

### 第三轮：用户接口与运维入口检查

检查问题：简单场景是否简洁、危险操作是否显式、Python/Admin/CLI/Dashboard 是否一致、旧 API 是否可迁移。

结论：采用 `push()` 兼容层 + `enqueue(TaskSpec)` 完整层；采用 `EnqueueResult` 替代 `Optional[str]`；批量接口使用 TaskSpec 列表；危险重复使用枚举；所有入口复用同一业务层。

## 23. 最终使用约束

即使完整实施本设计，使用方仍必须遵守：

1. 结果落库使用 logical key 对应的唯一约束或幂等 upsert；
2. 所有外部请求设置 timeout；
3. Scheduler 使用确定性计划 slot 和交易日历；
4. Reconciler 检查缺失业务分区，不能把“下一轮成功”当成“上一轮已补齐”；
5. 实时、回补、发现、详情抓取按 SLA 分队列；
6. 永久新闻/实体唯一性保存在业务数据库，不依赖 Redis 永久存活；
7. provider 限流和账号配额在业务客户端或共享 limiter 中实现；
8. qtask_list 提供 at-least-once，不宣称 exactly-once。

在这些边界下，qtask_list 可以稳定覆盖股票历史下载、周期逐只抓取、同日多轮股票列表、新闻动态增长和人工数据修复，同时保持库本身职责清晰。
