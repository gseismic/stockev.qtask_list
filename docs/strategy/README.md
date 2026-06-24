# qtask_list 策略思想

## 文档定位

本文档描述 qtask_list 的长期策略思想：它不是一份接口说明，也不是一份实现计划，而是回答三个问题：

1. 这个库最终要解决什么问题。
2. 当前设计为什么采取这种取舍。
3. 后续迭代应该用什么原则判断优先级。

qtask_list 的核心目标，是成为一个低依赖、可恢复、可诊断、可运维的 Redis 任务队列基础设施。它优先服务需要快速落地异步任务、多级流水线、Agent 调度和业务批处理的场景，而不是追求替代 Kafka、RabbitMQ 或 Celery 的所有能力。

## 核心判断

qtask_list 的战略起点是一个现实判断：很多业务系统需要的是“足够可靠、容易理解、容易运维”的任务队列，而不是一个复杂的分布式消息平台。

因此本库的第一性原则是：

> 用 Redis 已有的数据结构表达清晰的任务状态，用少量稳定概念覆盖任务从创建、执行、失败、恢复到归档的完整生命周期。

这带来几个关键取舍：

1. 选择 Redis List 和 Sorted Set，而不是引入专用消息中间件。
2. 选择显式状态队列，而不是把任务状态隐藏在 Worker 内部。
3. 选择可靠消费和可恢复性优先，而不是先追求极致吞吐。
4. 选择 CLI、Dashboard、QueueAdmin 形成运维闭环，而不是只提供生产消费 API。
5. 选择压缩、外存、归档控制 Redis 边界，而不是默认把所有数据永久塞进 Redis。

## 系统心智模型

qtask_list 应该始终保持一个简单心智模型：

```text
Producer -> ready -> processing -> ack -> history
                         |
                         -> fail -> retry -> ready
                                  -> dlq

delay -> ready
stale processing -> ready
history -> archive
large payload -> RemoteStorage
```

这个模型比具体 Redis key 更重要。用户应该先理解“任务处于什么状态、下一步可以做什么”，而不是先理解底层 key 如何拼接。

## 用户接口策略

用户接口面向业务开发者、Agent 调用者和运维人员，目标是低认知负担表达真实意图。

### SmartQueue

`SmartQueue` 是生产和消费的核心接口。它应该保持直接：

- `push` 表达投递任务。
- `pop` 表达领取任务。
- `ack` 表达完成。
- `fail` 表达失败并进入重试或 DLQ。
- `move_delay`、`move_retry`、`recover` 表达明确的状态迁移。

策略上，`SmartQueue` 不应该暴露过多 Redis 实现细节。它可以让高级用户传入 Redis client、storage、processing key 等扩展点，但默认路径必须保持简单。

### Worker

`Worker` 面向业务处理逻辑。它的使用心智应该是：

1. 注册 action handler。
2. 启动 worker。
3. 让框架负责可靠消费、失败重试、心跳、优雅停止和下游投递。

Worker 的策略重点不是“更像一个通用调度框架”，而是让任务处理者少写容易出错的消费样板代码。

### QueueAdmin、CLI、Dashboard

管理接口不是附属功能，而是生产可用性的核心部分。

当任务失败、积压、卡在 processing、进入 DLQ 或需要重放时，系统必须给出可理解、可执行、可验证的操作入口。`QueueAdmin` 是复用层，CLI 和 Dashboard 是不同人机界面。

策略上，应优先把关键运维能力沉淀到 `QueueAdmin`，再由 CLI 和 Dashboard 复用，避免同一类状态迁移逻辑在多个入口重复实现。

## 内部接口策略

内部接口面向系统正确性、可测试性和演进性。它可以贴近 Redis 模型，但必须有清晰边界。

### 状态边界

任务状态必须显式、可枚举、可诊断：

- `ready`：等待消费。
- `processing`：已被 Worker 领取，尚未 ack/fail。
- `retry`：失败但仍可重试。
- `dlq`：重试耗尽或需要人工处理。
- `delay`：尚未到期。
- `history`：生命周期记录。

状态迁移应该尽量原子，尤其是从 processing 移出任务、移动 delay 到 ready、单任务重放和删除这类操作。状态迁移失败时，应优先保持任务可见，而不是静默丢弃。

### Worker 归属

Worker 专属 `processing:{worker_id}` 和 heartbeat 是可靠恢复策略的关键。它解决的问题是：系统不能因为一个 Worker 启动或恢复动作，就抢走其他活跃 Worker 正在处理的任务。

因此 recovery 的默认策略必须保守：只恢复 stale worker 的 processing；强制恢复活跃 Worker 必须显式。

### 历史记录

历史记录是诊断、回放和审计的基础，不只是附加日志。任务进入系统后，应能通过 task_id 看到它的关键生命周期状态。

历史记录也必须有生命周期边界。Redis 不是长期审计仓库，所以 TTL 和归档是基础能力，不应等到数据膨胀后再补救。

## 失败处理策略

qtask_list 应把失败视为正常路径，而不是异常路径。

核心策略是：

1. 业务处理失败进入 retry。
2. 重试耗尽进入 DLQ。
3. Worker 崩溃后，stale processing 可恢复。
4. 格式异常或无法解析的毒性消息应移入 DLQ，避免永久阻塞。
5. 管理端必须支持查看、诊断、单条重放、批量重放和删除。

这套策略的目标不是让失败消失，而是让失败可见、可控、可恢复。

## Redis 边界策略

Redis 适合做快速状态存储，不适合无限制保存大 payload 和长期历史。

因此 qtask_list 对 Redis 的使用策略是：

1. 队列状态保存在 Redis。
2. 大 payload 优先走 RemoteStorage。
3. 无外存时可压缩保存，降低 Redis 压力。
4. 历史记录有 TTL。
5. 过期或长期历史可归档到 SQLite。
6. 常见批量操作使用 pipeline 或 Lua，降低 RTT 和状态不一致风险。

这个策略保护的是系统的长期可运维性。短期方便不能以 Redis 不可控膨胀为代价。

## 运维闭环策略

一个任务队列只要进入生产，就一定会遇到以下问题：

- 为什么任务没有被消费。
- 哪些任务失败了。
- 某个 Worker 是否还活着。
- processing 里的任务能不能恢复。
- DLQ 里的任务是否可以重放。
- retry 和 delay 是否正常流转。
- Redis 内存是否接近风险边界。

qtask_list 的策略不是把这些问题留给用户自己拼 Redis 命令，而是把它们产品化为：

- 统一的 `QueueAdmin` 能力。
- 面向终端的 CLI。
- 面向远程查看和操作的 Dashboard。
- 面向保留和容量控制的 history/archive/monitor。

后续迭代中，凡是能显著降低运维不确定性的能力，都应被视为高价值任务。

## 演进优先级

后续任务可以按以下顺序判断优先级：

1. 数据安全：是否减少任务丢失、误恢复、重复处理、状态不一致的风险。
2. 可验证性：是否让测试、类型检查、运行时自检更稳定。
3. 可诊断性：是否让用户更快判断任务为什么没有按预期流转。
4. 运维效率：是否让常见恢复、重放、清理、查看动作更简单。
5. 接口清晰度：是否减少用户理解成本，或减少 CLI、Dashboard、QueueAdmin 的重复逻辑。
6. 性能边界：是否降低 Redis 压力、减少 RTT、控制 Worker 排队深度。
7. 扩展能力：是否在不污染核心接口的前提下支持更多存储、认证、部署或集成场景。

这个排序意味着：一个能防止错误恢复的改动，通常比一个小幅提升吞吐的改动更重要；一个能让 `pytest -q` 稳定反映本库质量的改动，也比增加新功能更基础。

## 不做什么

为了保持系统清晰，qtask_list 应明确避免以下方向：

1. 不追求成为通用消息中间件。
2. 不在用户接口中暴露过多 Redis key 细节。
3. 不为了表面简洁牺牲恢复语义和错误边界。
4. 不把 Dashboard 做成展示型页面，而应服务真实运维动作。
5. 不让 CLI、Dashboard、QueueAdmin 各自维护不同的状态迁移规则。
6. 不默认保存无限历史或无限大 payload。
7. 不在没有验证闭环的情况下扩展关键状态流转能力。

## 当前阶段判断

从当前代码状态看，qtask_list 已经形成了较完整的基础形态：

- 核心队列有 ready、processing、retry、dlq、delay、history 状态。
- Worker 已有专属 processing key、heartbeat、stale recovery、优雅停止和并发背压。
- CLI 和 Dashboard 已覆盖状态查看、任务重放、恢复、删除、清理、登录保护等运维动作。
- RemoteStorage、压缩、归档和监控已经开始处理 Redis 边界问题。
- 测试、ruff、mypy 的验证入口已稳定。

因此后续策略重点应从“补齐基本能力”转向“收紧接口边界和运维一致性”：

1. 将 CLI 中重复的队列管理逻辑逐步下沉到 `QueueAdmin`。
2. 将 RemoteStorage 服务端启动配置收敛为更明确的公开运行接口。
3. 强化单任务状态迁移、恢复和删除的契约测试。
4. 增强诊断输出，让系统能主动解释积压、失败和 stale worker。
5. 完善部署文档，说明基础安装、Dashboard 安装、Storage 安装和完整运维安装的差异。

## 一句话策略

qtask_list 的长期策略，是用 Redis 构建一个轻量但不轻率的任务队列：简单场景要足够简单，失败场景要足够可控，生产运维要有完整闭环，内部状态要始终可验证、可恢复、可演进。
