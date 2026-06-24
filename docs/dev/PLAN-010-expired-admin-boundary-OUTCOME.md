# PLAN-010 结果: 过期任务管理边界修正

## 完成内容

1. 修正 `QueueAdmin._read_expired()` 的边界语义：
   - 新增显式 `limit` 参数控制返回数量；
   - `scan_limit` 只控制 history 扫描范围；
   - 保留 2000 条扫描上限，避免无限扫描。
2. 修正调用点：
   - `list_expired(queue_name, limit)` 现在会尊重传入 limit；
   - `list_tasks(..., state=expired)` 不再被内部 50 条常量截断；
   - `requeue_expired(queue_name)` 使用默认 500 条批处理上限。
3. 修正过期任务放回后的状态一致性：
   - `_requeue_single_expired()` 放回 ready 后继续更新 `status=pending`；
   - 同时清空 `expires_at` 的有效值，避免任务仍被统计为 expired 并被重复放回。
4. 增加 Dashboard API 回归测试：
   - `limit=60` 的 expired API 返回 60 条；
   - `state=expired&limit=60` 的任务列表返回 60 条；
   - 批量 requeue expired 可以处理 60 条；
   - requeue 后这些任务不再出现在 expired 列表中。

## 涉及文件

| 文件 | 变更 |
|------|------|
| `qtask_list/admin.py` | 过期任务读取 limit/scan_limit 分离；批量放回默认上限；放回后清空 `expires_at` |
| `tests/test_dashboard_api.py` | 新增 2 个过期任务边界测试和批量 seed helper |
| `docs/dev/PLAN-010-expired-admin-boundary.md` | 本轮计划 |

## 验证结果

- `pytest tests/test_dashboard_api.py -q`：22 passed
- `pytest -q`：88 passed
- `ruff check .`：通过
- `mypy qtask_list cli dashboard remote_storage`：通过
- `git diff --check`：通过

## 后续建议

Dashboard 已支持 `completed` / `failed` / `expired` 状态，但 README 和 `qtask-list-usage` skill 的 QueueState/管理接口说明仍未完整覆盖这些 Dashboard 扩展。下一轮建议做文档与 skill 同步，避免 Agent 或业务开发者按旧枚举理解管理接口。
