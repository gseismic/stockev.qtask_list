# PLAN-007 CLI 读取、投递与清理逻辑收敛到 QueueAdmin

## 目标

在 PLAN-006 已经收敛高风险状态迁移动作的基础上，继续将 CLI 的读取、投递和清理入口改为复用 `QueueAdmin`，进一步统一 CLI、Dashboard 和 Agent 管理语义。

## 当前证据

1. `QueueAdmin` 已提供以下能力：
   - `list_queues`
   - `queue_stats`
   - `list_tasks`
   - `get_task`
   - `push_task`
   - `clear_queue`
2. `cli/__main__.py` 仍保留平行实现：
   - `list_all_queues`
   - `processing_keys`
   - `state_keys`
   - `get_queue_stats`
   - `read_state_messages`
   - `task_record`
   - `push` 中直接创建 `SmartQueue`
   - `clear` 中直接拼 Redis key 和 pipeline 删除

## 实施方案

1. CLI `status` 改为使用 `QueueAdmin.list_queues()` / `QueueAdmin.queue_stats()`。
2. CLI `push` 改为使用 `QueueAdmin.push_task()`。
3. CLI `peek` 改为使用 `QueueAdmin.list_tasks()`，并做 CLI 输出格式适配。
4. CLI `clear` 改为使用 `QueueAdmin.clear_queue()`。
5. CLI `task get` 改为使用 `QueueAdmin.get_task()`。
6. 删除 CLI 中不再使用的读取 helper。
7. 补充测试：
   - `clear --no-dlq` 保留 DLQ。
   - `peek --state processing` 能读取 worker-specific processing。
   - `push --namespace` 仍返回规范队列名。

## 非目标

1. 不改 `watch` 的循环展示逻辑。
2. 不改 `history` 和 `clean-history` 的 TTL / 历史维护逻辑。
3. 不改变 CLI 用户可见命令参数。

## 验证

- `pytest tests/test_cli.py -q`
- `pytest -q`
- `ruff check .`
- `mypy qtask_list cli dashboard remote_storage`
