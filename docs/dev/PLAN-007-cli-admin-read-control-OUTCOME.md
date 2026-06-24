# PLAN-007 CLI 读取、投递与清理逻辑收敛到 QueueAdmin 结果

## 实施结果

1. CLI `status` 改为使用 `QueueAdmin`：
   - 指定队列使用 `queue_stats()`。
   - 全量队列使用 `list_queues()`。
2. CLI `push` 改为使用 `QueueAdmin.push_task()`：
   - 保留 `--namespace` 组合队列名语义。
   - JSON 输出继续返回 `queue`、`task_id`、`delay_seconds`。
3. CLI `peek` 改为使用 `QueueAdmin.list_tasks()`：
   - 新增 `cli_task_row()` 做字段适配。
   - CLI 输出继续保留 `state`、`source`、`raw` 等旧格式字段。
4. CLI `clear` 改为使用 `QueueAdmin.clear_queue()`：
   - 保留 `--include-history` 和 `--no-dlq` 行为。
5. CLI `history -t` 和 `task get` 改为使用 `QueueAdmin.get_task()`。
6. 删除 CLI 中不再使用的读取 helper：
   - `parse_json_value`
   - `task_record`
   - `decode_queue_message`
   - `list_all_queues`
   - `state_keys`
   - `read_state_messages`
7. 补充 CLI 测试：
   - `push --namespace --json` 返回规范队列名。
   - `peek --state processing --json` 能读取 worker-specific processing key。
   - `clear --no-dlq` 会保留 DLQ。

## 行为影响

1. CLI 的读取、投递和清理入口进一步复用 `QueueAdmin`，降低 CLI、Dashboard、Agent 管理语义分叉风险。
2. `peek` 的 JSON 输出通过适配层保留旧字段，兼容已有 CLI 使用习惯。
3. `status <queue>` 现在可以展示 `QueueAdmin.queue_stats()` 返回的扩展统计字段，包括 history、active_workers、stale_workers。

## 验证结果

- `pytest tests/test_cli.py -q`：20 passed
- `pytest -q`：72 passed
- `ruff check .`：All checks passed
- `mypy qtask_list cli dashboard remote_storage`：Success

## 后续建议

1. `watch` 仍使用 CLI 本地 `get_queue_stats()`，后续可以改为周期性调用 `QueueAdmin.queue_stats()`。
2. `history` 列表和 `clean-history` 仍直接使用 `SmartQueue.history`，后续可以评估是否为 `QueueAdmin` 增加历史清理和列表接口。
3. 发布前应更新 qtask-list skill 文档中的安装说明，补充 `storage` optional extra。
