# PLAN-011 结果: Dashboard 状态能力文档同步

## 完成内容

1. 更新 `README.md` 的 `QueueAdmin` 示例：
   - 增加 `QueueState.completed` / `QueueState.failed` / `QueueState.expired` 查询示例；
   - 增加 `completed_after` 时间筛选示例；
   - 增加 `push_task(..., expire_seconds=...)` 业务过期示例；
   - 增加 `list_expired()` / `requeue_expired()` 示例；
   - 增加 `delete_queue()` 危险操作示例；
   - 补充完整 `QueueState` 用户可见状态枚举。
2. 更新 README 的 Dashboard 功能清单：
   - 状态视图扩展为 ready/processing/retry/dlq/delay/completed/failed/expired/history；
   - 增加创建时间/完成时间筛选；
   - 增加过期任务放回；
   - 增加 delay 与 `expire_seconds` 投递；
   - 增加删除队列及关联历史记录。
3. 更新 `skills/qtask-list-usage/SKILL.md`：
   - `SmartQueue.push()` 示例增加 `expire_seconds`；
   - `QueueAdmin` 示例与 README 对齐；
   - `list_queues()` 返回字段说明补充 completed/failed/expired；
   - Dashboard 功能清单与 README 对齐；
   - 常见陷阱补充 `expired` 与历史 TTL 清理的区别。
4. 明确概念边界：
   - `expired` 是业务任务过期状态；
   - `clean-history` / `clean_expired()` 是历史记录 TTL 清理；
   - 二者不应混用。

## 涉及文件

| 文件 | 变更 |
|------|------|
| `README.md` | 同步 QueueAdmin、QueueState、Dashboard 过期任务与状态能力说明 |
| `skills/qtask-list-usage/SKILL.md` | 同步 Agent 使用指南中的 API 示例、状态枚举、Dashboard 功能和常见陷阱 |
| `docs/dev/PLAN-011-doc-dashboard-state-sync.md` | 本轮计划 |

## 验证结果

- `rg` 关键字检查：README 与 skill 均包含 completed/failed/expired、`expire_seconds`、`list_expired`、`requeue_expired`、`delete_queue`
- `rg` 旧状态描述检查：未发现旧的 ready/processing/retry/dlq/delay/history-only 描述
- `pytest -q`：88 passed
- `ruff check .`：通过
- `mypy qtask_list cli dashboard remote_storage`：通过
- `git diff --check`：通过

## 后续建议

下一轮建议继续审查 Dashboard/API 的用户接口边界，优先看时间筛选和 completed/failed 查询在大量 history 下的扫描策略是否会漏查或性能退化。
