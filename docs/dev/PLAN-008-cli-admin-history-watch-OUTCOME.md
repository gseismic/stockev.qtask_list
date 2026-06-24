# PLAN-008 CLI 历史与监控入口收敛到 QueueAdmin 结果

## 实施结果

1. `QueueAdmin` 新增 `clean_history()`：
   - 支持指定队列清理。
   - 支持全队列清理。
   - 支持传入 `ttl_days`。
   - 返回总清理数和按队列统计。
2. CLI `watch` 改为使用 `QueueAdmin.queue_stats()`。
3. CLI `history <queue>` 改为使用 `QueueAdmin.list_tasks(..., state="history")`。
4. CLI `clean-history` 改为使用 `QueueAdmin.clean_history()`。
5. 删除 CLI 中不再使用的本地队列 helper：
   - `parse_queue_name`
   - `queue_from_name`
   - `processing_keys`
   - `get_queue_stats`
6. 补充 CLI 测试：
   - `history <queue>` 能通过统一历史读取显示任务。
   - `clean-history <queue>` 会清理指定队列过期历史。
   - `clean-history` 无队列参数时会清理多个队列。

## 行为影响

1. CLI 的实时监控、历史读取和历史清理现在复用 `QueueAdmin`，进一步统一 CLI、Dashboard 和 Agent 管理语义。
2. `clean-history` 的用户可见输出保持兼容。
3. `watch` 的显示字段保持兼容，内部数据来源改为统一管理接口。

## 验证结果

- `pytest tests/test_cli.py -q`：22 passed
- `pytest -q`：74 passed
- `ruff check .`：All checks passed
- `mypy qtask_list cli dashboard remote_storage`：Success

## 后续建议

1. CLI 仍有 `archive` 和 `monitor` 直接使用底层模块，属于功能边界较明确的运维工具；后续可按需决定是否纳入 `QueueAdmin`。
2. 发布前同步更新 `skills/qtask-list-usage/SKILL.md`，反映 `storage` optional extra 和 CLI 管理逻辑已经统一到 `QueueAdmin`。
