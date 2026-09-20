# PLAN-015：qtask_list V2 整体设计实施

- 创建时间：2026-09-20 15:12（Asia/Shanghai）
- 实施依据：`docs/design/qtask-overall-20260920-v2.md`
- 目标版本：`0.2.0`
- 范围：Python SDK、Worker、Redis 数据模型、RemoteStorage、历史归档、QueueAdmin、CLI、Dashboard、示例与使用文档
- 基线：commit `44eb587`；`pytest` 114 passed，mypy 通过；ruff 因 `tests/manual/verify_review_claims.py` 两处 E702 未通过
- 工作树保护：`AGENTS.md` 是用户已有未提交改动，本计划不得修改或提交该文件

## 1. 实施目标

把当前 V1 的“payload 内嵌运行元数据 + TTL 去重 + 可重新打开终态”实现升级为设计稿规定的 V2：

1. 用 `TaskSpec` 表达完整任务语义，用 `EnqueueResult` 返回结构化投递结果；保留 `push()` / `push_batch()` 兼容层。
2. 新任务默认写 V2 信封，运行元数据不再污染业务 payload；Worker 兼容读取 V1 信封。
3. 使用 Redis 原子状态转换实现 live identity、终态不可变、精确 attempt、deadline、重试、DLQ 与 replay。
4. 让 SDK、Admin、CLI、Dashboard 共用同一投递与状态转换能力。
5. 完成 RemoteStorage 错误分类和保留期协议、历史清理/归档正确性、运维诊断与危险操作确认。
6. 实施 keyed concurrency、latest-only supersede、`TaskResult.emissions`、maintenance leader 等阶段 D 能力。
7. 同步 README、示例、仓库内 skill、版本号和行为变更说明。

## 2. 公共 API 与核心类型

### 2.1 新增类型

- `TaskSpec`：不可变 dataclass，包含 action、payload、logical_key、scheduled_for、not_before_at、start_deadline_at、dedup_until、trace_id、parent_task_id、concurrency_key、supersede_key、supersede_version。
- `EnqueueResult`：accepted、task_id、logical_key、duplicate_of、reason。
- `DuplicateAction`：`REJECT` / `ALLOW_NEW`。
- `IdentityPolicy`：`KEEP` / `RELEASE`。
- `TaskContext`：只读执行上下文及协作式停止信号。
- `TaskResult`：业务返回值和下游 `TaskSpec` emissions。
- `RetryableTaskError` / `PermanentTaskError`：带稳定错误码；前者支持 retry_after。
- `HistoryMode`：`FULL` / `MINIMAL`；旧 `record_history=False` 映射到 minimal。
- `Clock` 协议、`SystemClock` 和测试时钟。

### 2.2 校验规则

- action 非空并限制 UTF-8 长度；payload 必须是可 JSON 序列化的 object。
- 所有 datetime 必须带时区，内部统一为 UTC epoch。
- logical_key 空字符串非法，明文不超过 512 UTF-8 bytes。
- dedup_until 依赖 logical_key；supersede_key/version 必须成对出现。
- not_before_at 不得晚于 start_deadline_at；dedup_until 早于 deadline 时发出明确警告。
- `enqueue_many()` 单批最多 1000 项，按输入顺序返回等长结果。

### 2.3 兼容策略

- `push()` 把 payload.action 转成 `TaskSpec.action`，本版本仍返回 `str | None` 并发出废弃提醒。
- `push_batch()` 保留平行数组参数并适配 `enqueue_many()`。
- `max_retry` 作为 `max_attempts` 的兼容别名；冲突参数拒绝。
- `expires_at`、`_retry`、`_large`、`_compressed` V1 消息通过兼容解码器转成内部 V2 模型。

## 3. 分阶段实施

### 阶段 A：立即正确性修复

1. 在只解析信封头后、解压/外存读取前检查 deadline。
2. RemoteStorage 区分 object missing、认证/配置、限流、瞬时网络/5xx、checksum、JSON 解码错误。
3. 外存瞬时错误进入统一 attempt/退避预算；永久错误直接 failed + DLQ。
4. `clean_history` 不删除任何仍有 operational message 的记录；skipped 不再计入 deadline_missed。
5. Admin 完整持有 storage、clock、重试、历史等 SmartQueue 配置。
6. 所有 admin move 在 moved=0 时不修改任务记录。
7. 删除“只改历史 deadline、不改消息”的过期重放路径，改为新 task_id replay。
8. 修复手动验证脚本 ruff 问题并更新其 V2 假设。

### 阶段 B：V2 信封、身份与原子状态机

1. 新增 V2 envelope codec：inline/zstd/external 三种 payload 表示、版本化头部和 V1 双读。
2. 新增队列注册表与哈希化 dedup owner key，owner 保存 logical_key、task_id、generation、outcome、dedup_until。
3. 实现并复用原子转换：
   - enqueue_v2；
   - begin_attempt；
   - complete_task；
   - retry_task；
   - fail_task；
   - skip_task / cancel_task；
   - replay_task；
   - admin_move / purge_task。
4. 每个脚本校验消息所有权和 generation；moved=0 零副作用；重复调用不产生重复消息；返回稳定结果码。
5. live owner 不设普通 TTL；终态时按 dedup_until 精确保留或 compare-and-delete。
6. `ALLOW_NEW` 创建新实例、提升 generation、记录 `duplicate_override_of`，新实例成为 owner。
7. attempt 在 begin_attempt 时由 0 增至 1；deadline/supersede 命中不消耗；所有实际失败（含外存）消耗。
8. 自动重试统一进入 delay ZSET；若下一次运行不早于 deadline，直接 skipped。
9. terminal outcome 不可重新打开；DLQ 是 operational location，不是 outcome。
10. DLQ replay 创建新 task_id，写 `replay_of` / `replayed_by`，原 failed/skipped 记录保持不变。
11. live、DLQ 记录不设置历史 TTL；终态且无 operational message 后才开始 TTL。

### 阶段 C：入口、运维与业务示例

1. QueueAdmin 新增 `enqueue` / `enqueue_many` / `replay_task`，所有入口委托 SmartQueue。
2. QueueState 增加 retry_wait、cancelled、deadline_missed；expired 保留兼容别名。
3. CLI push/replay/clear/delete 支持 V2 字段、结构化 JSON 和危险操作确认。
4. Dashboard 后端支持同一投递字段、RemoteStorage 配置、replay、identity policy 与确认参数。
5. Dashboard 前端增加高级投递选项、cancelled/deadline_missed 视图、duplicate/replay 跳转与结果展示。
6. 统计/诊断至少覆盖投递结果、各 location 深度、attempt/retry/deadline/payload/recovery/replay/supersede、DLQ 最老年龄。
7. maintenance 增加限速一致性诊断与最小 DLQ/heartbeat/deadline 告警回调。
8. 重写 `examples/stockev/`，增加 scheduler、确定性 5 分钟 slot、历史分区、早晚 universe、新闻 discover/fan-out、Reconciler 示例。

### 阶段 D：高级执行控制

1. keyed concurrency：带 token 的分布式 lease、续租、compare-and-delete；争锁失败短延迟且不消耗 attempt。
2. supersede：维护最新版，begin_attempt 前命中过时版本则 cancelled，不读取 payload、不消耗 attempt。
3. `TaskResult.emissions`：支持带身份的下游 fan-out；兼容 dict 下游投递。
4. maintenance/archive leader lock，避免同队列多 Worker 同时归档。
5. RemoteStorage `retain_until` 延长协议、sha256/size 校验和 best-effort orphan 清理钩子。
6. internal consistency report：孤儿 owner、无消息 live task、terminal 残留、stale history index、无 heartbeat processing、外存保留期风险；确定所有权时才修复。

## 4. 代码与文档改动范围

- 新增核心模块（按实现需要）：模型/时钟、信封 codec、状态转换、错误/指标。
- 重构：`qtask_list/queue.py`、`worker.py`、`history.py`、`admin.py`、`storage.py`、`archiver.py`、`__init__.py`。
- 更新：`remote_storage/server.py`、`cli/__main__.py`、`dashboard/main.py` 及静态资源。
- 新增/更新测试：模型、V2 queue、并发/故障注入、worker、admin、CLI、Dashboard、storage、archive、手动 E2E。
- 更新：`README.md`、`examples/`、`skills/qtask-list-usage/SKILL.md`、`pyproject.toml`。
- 生成：`docs/dev/PLAN-015-qtask-v2-OUTCOME.md`；追加 `docs/dev/INDEX.md`。

## 5. 验证矩阵

### 5.1 身份与时间

- 同 slot 重复返回 duplicate_of；下一 slot 接受。
- live 任务在 dedup_until 过后仍拒绝；终态后精确释放/保留。
- enqueue 原子失败不产生 owner/task/message 半状态。
- ALLOW_NEW 切换 owner 且记录覆盖 lineage。
- timezone-aware 校验、确定性 scheduled_for、absolute deadline 全部可审计。

### 5.2 状态与故障

- inline/zstd/external handler payload 完全一致，运行元数据不泄漏。
- 过期 external 不下载；404 永久失败；timeout/5xx 消耗 attempt 并退避；预算和 deadline 均生效。
- begin/ack/retry/fail/skip/cancel 重入安全；moved=0 不改 history。
- ready/processing/delay/retry/DLQ 存在时 history 不被删除或归档。
- replay 新 task_id，原终态不变；archive 仅终态且无 operational message。

### 5.3 Worker 与高级能力

- 单参数/双参数 handler 在注册时校验；TaskContext 字段正确。
- concurrency lease 串行同 key、并行不同 key，崩溃后可恢复。
- superseded 在外存读取前取消且不消耗 attempt。
- TaskResult emissions 使用 TaskSpec 语义并支持父子追踪。
- stale recovery、leader maintenance、优雅 drain 不破坏所有权。

### 5.4 多入口与工程质量

- 同一 TaskSpec 经 Python/Admin/CLI/Dashboard 行为一致。
- Dashboard RemoteStorage 真正参与 enqueue 和 resolve。
- 危险覆盖、强制恢复、identity release、删除、批量 replay 都需显式确认。
- `python -m pytest tests/ -q`、`python -m ruff check .`、`python -m mypy qtask_list cli dashboard remote_storage` 全绿。
- 更新后的所有手动 E2E 和示例可运行，且 Redis/临时文件清理干净。
- 逐项复核设计稿 §21，所有条目有测试、命令输出或当前文件作为直接证据。

## 6. Review 要点

1. Redis 原子脚本 key/argv 顺序、幂等性、compare-and-delete 与边界返回码。
2. BRPOPLPUSH 到 begin_attempt 之间的崩溃恢复语义。
3. V1/V2 双读及兼容 API 是否破坏既有调用方。
4. terminal 不可变、location/outcome 正交和 history TTL 起点。
5. 外存预上传后的失败/不确定结果、校验和与保留期。
6. 管理入口是否绕过统一状态机，危险操作是否有确认。
7. 多线程 Worker 的 codec、heartbeat、lease、executor 和 stop 竞态。
8. 文档/示例/skill 是否与实际签名和默认行为一致。

## 7. 提交与推送

实施、review、测试、示例运行和结果文档完成后：

1. 检查 `git diff`，确保不包含用户修改的 `AGENTS.md`。
2. 使用中文详细 commit message，明确列出：
   - `docs/dev/PLAN-015-qtask-v2.md`
   - `docs/dev/PLAN-015-qtask-v2-OUTCOME.md`
   - 新增设计/迁移说明（如有）
3. 提交后立即 `git push`，再核对本地 HEAD 与 `origin/main` 一致。

## 8. 收尾说明

本计划的核心实现、前端回归和 Python 侧 V2 语义收尾已经完成。旧测试迁移、V1 双读
管理降级、文档/示例同步及最终验证由 `PLAN-016-v2-review-remediation.md` 承接；
整体结果见 `PLAN-015-qtask-v2-OUTCOME.md`。
