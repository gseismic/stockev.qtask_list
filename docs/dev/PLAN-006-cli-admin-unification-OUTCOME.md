# PLAN-006 CLI 管理逻辑收敛到 QueueAdmin 结果

## 实施结果

1. CLI 引入 `QueueAdmin` 作为任务管理操作执行层：
   - 新增 `admin_from_url()`。
   - 复用 `QueueAdmin(redis_url=...)`。
2. 以下 CLI 命令已改为调用 `QueueAdmin`：
   - `requeue`
   - `retry`
   - `recover`
   - `task requeue`
   - `task delete`
3. 删除 CLI 中不再使用的重复状态迁移 helper：
   - `drain_list_to_ready`
   - `recover_processing_key`
   - `recover_stale_processing_cli`
   - `move_exact_list_message`
   - `move_exact_delay_message`
   - `move_task_to_ready`
   - `remove_task_from_list_key`
   - `remove_task_from_delay_key`
   - `remove_task_from_queues`
   - `message_task_id`
   - `queue_bases_with_state_keys`
4. 补充 CLI 测试断言：
   - bulk DLQ requeue 后对应历史状态更新为 `pending`。
   - retry drain 后对应历史状态更新为 `pending`。

## 行为影响

1. CLI、Dashboard 和 API 管理入口现在复用同一套状态迁移语义。
2. CLI bulk DLQ requeue / retry drain 会与 `QueueAdmin` 一样更新已存在的 history 状态。
3. 单任务重放、删除、recover 的用户可见输出保持兼容。

## 验证结果

- `pytest tests/test_cli.py -q`：17 passed
- `pytest -q`：69 passed
- `ruff check .`：All checks passed
- `mypy qtask_list cli dashboard remote_storage`：Success

## 后续建议

1. 继续将 CLI 的 `clear`、`push`、任务读取和队列统计能力逐步收敛到 `QueueAdmin`。
2. 将 Dashboard 与 CLI 的错误信息和返回结构进一步对齐，降低 Agent 调用时的分支处理成本。
