# PLAN-010: 过期任务管理边界修正

## 背景

Dashboard UX v2 已新增 `expired` 状态、过期任务列表和批量放回能力。但当前 `QueueAdmin._read_expired()` 内部固定在 50 条后停止，导致：

- `GET /api/queue/{name}/expired?limit=100` 实际最多返回 50 条；
- `QueueAdmin.list_tasks(..., state=expired, limit>50)` 也会被内部常量截断；
- `POST /api/queue/{name}/requeue-expired` 批量放回最多处理 50 条，和用户对“批量放回”的理解不一致。

这属于用户接口契约和内部实现边界不一致，优先级高于继续扩展新能力。

## 目标

1. 让过期任务列表尊重调用方传入的 `limit`。
2. 让批量放回过期任务使用清晰的批处理上限，而不是隐式 50 条。
3. 过期任务放回 ready 后，清除历史记录中的过期标记，避免仍被统计为 expired 并重复入队。
4. 保持扫描有上限，避免对超大 history ZSET 做无限遍历。
5. 增加回归测试覆盖 `limit > 50`、批量放回超过 50 条，以及放回后不再列为过期。

## 实施方案

1. 调整 `QueueAdmin._read_expired()`：
   - 增加显式 `limit` 参数；
   - `scan_limit` 只控制扫描多少个 history id；
   - 返回数量由 `limit` 控制。
2. 调整调用点：
   - `list_expired(queue_name, limit)` 传入用户 limit；
   - `list_tasks(..., state=expired)` 使用更高的读取上限以支持后续过滤；
   - `requeue_expired(queue_name)` 使用明确的批处理上限。
3. 调整 `_requeue_single_expired()`：
   - 放回 ready 后将历史状态更新为 `pending`；
   - 同时清除 `expires_at` 字段的有效值，防止任务继续命中过期判断。
4. 测试：
   - Dashboard expired API 在 `limit=60` 时返回 60 条；
   - 批量 requeue expired 可以处理 60 条，并把历史状态更新为 `pending`；
   - requeue 后再次查询 expired 不再返回这些任务。

## 非目标

- 不改变前端交互布局。
- 不新增基于 `expires_at` 的独立 Redis 索引。
- 不解决超大历史集的完整分页问题；本轮只修正当前 API 已声明的边界。

## 验证

- `pytest tests/test_dashboard_api.py -q`
- `pytest -q`
- `ruff check .`
- `mypy qtask_list cli dashboard remote_storage`
