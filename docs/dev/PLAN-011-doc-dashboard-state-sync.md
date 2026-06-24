# PLAN-011: Dashboard 状态能力文档同步

## 背景

当前代码已经支持 Dashboard / `QueueAdmin` 的扩展状态与操作：

- `QueueState.completed`
- `QueueState.failed`
- `QueueState.expired`
- `QueueAdmin.list_expired()`
- `QueueAdmin.requeue_expired()`
- `QueueAdmin.list_tasks()` 的时间范围筛选
- `QueueAdmin.push_task(..., expire_seconds=...)`

但 `README.md` 和 `skills/qtask-list-usage/SKILL.md` 仍主要停留在 ready/processing/retry/dlq/delay/history 视角，`QueueState` 枚举也未列出新增状态。这会导致业务开发者、运维人员和 Agent 调用者低估当前管理接口能力，甚至按旧状态模型写脚本。

## 目标

1. 同步 README 的 `QueueAdmin` 示例和 Dashboard 功能清单。
2. 同步 `qtask-list-usage` skill 的 `QueueAdmin` 示例、`QueueState` 枚举、Dashboard 功能清单和常见陷阱。
3. 明确 `expired` 是用户可见状态，和历史 TTL 清理不是同一概念：
   - `expire_seconds` / `expires_at` 表示业务任务过期；
   - `clean-history` / `clean_expired()` 表示历史记录 TTL 清理。
4. 不修改运行时代码。

## 实施方案

1. 更新 `README.md`：
   - 在 `QueueAdmin` 代码示例中加入 completed/failed/expired 查询、时间筛选、`list_expired()`、`requeue_expired()`、`expire_seconds` 投递；
   - 补充 `QueueState` 枚举说明；
   - 更新 Dashboard 功能清单，覆盖 completed/failed/expired、时间筛选、过期任务放回和队列删除。
2. 更新 `skills/qtask-list-usage/SKILL.md`：
   - 与 README 保持同等 API 语义；
   - 增加 `expire_seconds` 示例和过期任务管理示例；
   - 更新 Dashboard 功能清单和常见陷阱。
3. 验证：
   - 用 `rg` 检查关键状态和方法在 README 与 skill 中均出现；
   - 运行 `pytest -q` 确认文档变更未影响测试环境；
   - 运行 `ruff check .` 和 `mypy qtask_list cli dashboard remote_storage` 保持提交门禁一致。

## 非目标

- 不重写完整用户手册。
- 不新增 CLI 命令。
- 不改变 Dashboard 前端文案。
