# qtask_list Dashboard 与管理接口设计

## 背景

`qtask_list` 的核心目标是为 Python 业务项目提供轻量、可嵌入、可恢复的 Redis 任务队列。`stockev.spiders` 是典型真实用户：每个 spider 使用 `stockev:<spider>` 命名空间，并固定拆成 `fetch` 与 `store` 两级队列。

这类用户日常不关心 Redis Key 细节，关心的是：

- 哪个 spider/队列积压了。
- 哪些任务失败了，失败 payload 是什么。
- 能否把 DLQ 或 retry 里的任务手动重试。
- 能否恢复崩溃 Worker 留下的 processing 任务，但不要抢活跃 Worker。
- 能否删除误投、脏数据或 poison task。
- 能否从一个页面完成观察与控制。

## 受众区分

### 用户接口

面向业务开发者、运维者、Agent 调用者。概念应该贴近真实意图：

- 队列：`stockev:day-kline:fetch`
- 状态：`ready`、`processing`、`retry`、`dlq`、`delay`、`history`
- 操作：查看、重试、恢复、删除、清空、投递

用户接口不要求理解 `BRPOPLPUSH`、worker-specific processing key、Redis ZSET 等实现细节。

### 内部接口

面向 CLI、Dashboard、测试和未来服务集成。内部接口需要稳定表达状态机与安全边界：

- 明确来源状态。
- 明确危险操作是否需要显式参数。
- 明确是否会移动 active worker 的 processing 任务。
- 返回结构化结果，方便 CLI、Dashboard 和 Agent 复用。

## 从 stockev.spiders 反推的场景

### 场景 1：启动三终端流水线后观察状态

用户启动：

```bash
stockev-spiders day-kline store
stockev-spiders day-kline spider
stockev-spiders day-kline generate --limit 3
```

Dashboard 应直接显示：

- `stockev:day-kline:fetch`
- `stockev:day-kline:store`
- 每条队列的 ready/processing/retry/dlq/delay 数量
- 活跃 Worker 与 stale processing

### 场景 2：抓取失败后手动重试

用户看到 `stockev:sector-em:fetch` 有 DLQ，期望：

1. 点击队列。
2. 切到 DLQ。
3. 查看失败 payload 和 reason。
4. 对单个任务执行重试，或批量重放 DLQ。

不应要求用户手写 Redis 命令或理解 DLQ 的 key 名称。

### 场景 3：误投任务或某个 symbol 卡住

`stockev.spiders` 当前已有各 spider 自己的 admin delete 命令，说明“按任务内容定位并删除”是真实需求。`qtask_list` Dashboard 至少应支持：

- 搜索 payload。
- 删除单个 task_id。
- 删除时同时清理队列消息和 history 索引。

后续可以扩展成按 payload 字段过滤删除。

### 场景 4：Worker 崩溃

恢复 processing 默认只应恢复 stale worker 的任务。active worker 的 processing 必须保留，除非用户显式选择强制恢复。

## 设计决策

### 1. 增加共享管理接口 `QueueAdmin`

新增 `qtask_list.admin.QueueAdmin`，作为 Dashboard 与未来 CLI/Agent 的共享内部接口：

- `list_queues()`
- `queue_stats(queue)`
- `list_tasks(queue, state, limit, search)`
- `get_task(task_id)`
- `requeue_task(queue, task_id, from_state)`
- `requeue_dlq(queue, task_id=None)`
- `move_retry(queue)`
- `recover(queue, include_active=False)`
- `delete_task(task_id, queue=None)`
- `clear_queue(queue, include_dlq=True, include_history=False)`
- `list_workers(queue=None)`
- `diagnose(queue)`

这样 Dashboard 不直接拼 Redis key，避免前后端或 CLI 重复实现状态迁移。

### 2. Dashboard API 以用户意图建模

API 不暴露 Redis 命令，而暴露任务控制语义：

- `GET /api/queues`
- `GET /api/queue/{queue}/tasks?state=dlq`
- `GET /api/queue/{queue}/diagnose`
- `POST /api/queue/{queue}/retry`
- `POST /api/queue/{queue}/requeue-dlq`
- `POST /api/queue/{queue}/recover`
- `POST /api/task/{task_id}/requeue`
- `DELETE /api/task/{task_id}`

### 3. React 模块化 Dashboard

替换当前单文件 Vue Dashboard，改为 React + ES modules：

- `api.js`：后端 API 客户端
- `utils.js`：格式化与 JSON 工具
- `components.js`：队列列表、任务表、详情抽屉、操作区
- `app.js`：状态编排与自动刷新
- `app.css`：操作台样式

不引入构建链，保持本库轻量。React 通过 CDN 加载，模块代码由 FastAPI 静态服务提供。

### 4. Dashboard 信息架构

首屏应直接是可操作控制台：

- 顶部：连接状态、自动刷新、手动刷新。
- 左侧：队列列表，按名称搜索。
- 中间：队列统计、状态切换、任务表。
- 右侧/弹层：任务详情、JSON payload、单任务操作。
- 底部/侧边：安全操作按钮，例如批量 DLQ 重放、retry drain、stale recover。

危险操作必须显式：

- 清空队列。
- 删除任务。
- 强制恢复 active processing。

## 非目标

- 不在 qtask_list 内启动或管理外部业务 Worker 进程。
- 不替代 `stockev.spiders` 自己的 generate/query 命令。
- 不在第一版实现按 payload 字段批量删除。
- 不引入复杂前端构建系统。

## 验收标准

1. 有中文设计文档和实施计划。
2. Dashboard 后端 API 支持查看与控制任务生命周期。
3. Dashboard 前端基于 React，按模块拆分。
4. 可查看 ready/processing/retry/dlq/delay/history 任务。
5. 可手动执行 retry、DLQ requeue、单任务 requeue、delete、stale recover。
6. 测试覆盖主要 API 状态迁移。
7. `pytest`、`ruff`、`mypy` 通过。
