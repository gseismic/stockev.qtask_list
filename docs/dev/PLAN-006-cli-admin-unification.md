# PLAN-006 CLI 管理逻辑收敛到 QueueAdmin

## 目标

将 CLI 中高风险的任务状态迁移和删除逻辑收敛到 `QueueAdmin`，减少 CLI 与 Dashboard / API 管理入口之间的行为分叉。

## 当前证据

1. `qtask_list.admin.QueueAdmin` 已提供以下统一管理能力：
   - `move_retry`
   - `requeue_dlq`
   - `requeue_task`
   - `recover`
   - `delete_task`
2. `cli/__main__.py` 仍保留平行实现：
   - `drain_list_to_ready`
   - `recover_stale_processing_cli`
   - `move_task_to_ready`
   - `remove_task_from_queues`
3. 平行实现会带来两个实际风险：
   - CLI 与 Dashboard 的状态迁移语义可能逐步分叉。
   - bulk retry / requeue 这类操作是否更新 history 状态，容易在不同入口不一致。

## 实施方案

1. CLI 引入 `QueueAdmin` 作为任务管理操作的执行层。
2. 以下 CLI 命令改为复用 `QueueAdmin`：
   - `requeue`
   - `retry`
   - `recover`
   - `task requeue`
   - `task delete`
3. 移除 CLI 中不再使用的重复状态迁移 helper。
4. 补充 CLI 测试：
   - bulk DLQ requeue 会更新历史状态为 `pending`。
   - retry drain 会更新历史状态为 `pending`。
   - 现有单任务 requeue、delete、recover 行为保持不变。

## 验证

- `pytest -q`
- `ruff check .`
- `mypy qtask_list cli dashboard remote_storage`
