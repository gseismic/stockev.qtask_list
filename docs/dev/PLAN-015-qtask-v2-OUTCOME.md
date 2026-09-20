# PLAN-015：qtask_list V2 整体设计实施结果

- 计划文件：`docs/dev/PLAN-015-qtask-v2.md`
- 实施时间：2026-09-20
- 最终收尾：由 `docs/dev/PLAN-016-v2-review-remediation.md` 完成
- 目标版本：`0.2.0`

## 结果摘要

PLAN-015 的 V2 核心实现先由 `bb46a4d` 落地，Dashboard 和前端回归随后由
`fea08a4`、`c4372fc` 完成。本轮 PLAN-016 又把旧 V1 测试、双读管理路径、
`retry_wait` 统计、REST 4xx、Worker drain 和文档示例收口，因此 PLAN-015 的
整体验收项现在有可执行代码、测试或文档证据。

## 已交付能力

1. `TaskSpec`、`EnqueueResult`、`TaskContext`、`TaskResult` 及 `enqueue()` 统一了
   投递、执行上下文和下游 fan-out；`push()`/`push_batch()` 保留兼容层。
2. V2 信封、inline/zstd/external payload、任务记录中的 attempt/deadline、Lua
   原子状态转换、终态不可变、DLQ replay 新 task_id 和 replay 血缘已接入 SDK、
   Admin、CLI 与 Dashboard。
3. live identity 使用哈希 owner；在途不设置普通 TTL，终态按 `dedup_until`
   精确保留；V1 明文 owner、V1 retry List、V1 `expires_at`/`_retry` 可双读或安全
   降级迁移。
4. retry 统一进入 delay ZSET，`retry_wait` 由 `delay_reason=retry` 派生；过期任务
   使用 `start_deadline_at`，`deadline_missed`/`expired` 与 `skipped` 语义一致。
5. Worker 支持单/双参数 handler、协作式停止、keyed lease、supersede、emissions、
   stale recovery 与维护线程；停止信号不会在优雅 drain 中被维护线程清除。
6. QueueAdmin、CLI、Dashboard 共用 TaskSpec 与状态机；用户输入错误返回明确 4xx，
   危险恢复和批量管理要求显式确认。
7. `examples/stockev/` 已包含确定性 5 分钟调度、早晚 universe、历史分区、新闻
   discover/fan-out 和 Reconciler；README 与仓库内 skill 已同步 V2 语义。

## 验证证据

- `python -m pytest tests/ -q`：118 passed（PLAN-016 新增/迁移覆盖兼容消息、重试、
  deadline、replay、Dashboard 4xx 和 Worker 停止）。
- `python -m ruff check .`：通过。
- `python -m mypy qtask_list cli dashboard remote_storage`：19 个源文件通过。
- `node --check dashboard/static/js/*.js`：通过。
- `python -m compileall -q examples`：通过；调度器的确定性 slot/身份键 smoke test 通过。

## 未纳入本计划的后续方向

- 生产环境中的队列发现 SCAN 性能优化、RemoteStorage 孤儿清理和告警回调的真实
  外部集成仍需按业务部署环境单独验证。
- `AGENTS.md` 继续保留用户已有的未提交修改，本计划及相关提交不会包含它。
