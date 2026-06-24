# PLAN-012: History 状态筛选扫描边界修正

## 背景

Dashboard 已支持 `completed` / `failed` 状态视图和时间筛选，但当前 `QueueAdmin._read_history_by_status()` 只读取最近 `max(limit * 3, 300)` 条 history，再按 `status` 过滤。

当最近几百条都是 `pending`，而较早位置存在 `completed` 或 `failed` 时，用户请求 `state=completed` / `state=failed` 会返回空列表。这会让 Dashboard 误判“没有已完成/失败任务”，属于用户可见的正确性问题。

## 目标

1. 修复 `completed` / `failed` 状态稀疏时的漏查问题。
2. 修复 `completed` / `failed` 先按 `limit` 截断、再做时间筛选导致的漏查问题。
3. 保持扫描有上限，避免对超大 history ZSET 无限遍历。
4. 用 pipeline 批量读取 history hash，避免逐条 `get_task()` 造成大量 RTT。
5. 增加回归测试覆盖“最近 pending 遮挡较早 completed”和“最近 completed 不在时间范围内遮挡较早 completed”的场景。

## 实施方案

1. 调整 `_read_history_by_status()`：
   - 改为按批次 `zrevrange` 扫描 history；
   - 每批使用 pipeline 批量读取 `qtask:task:{id}`；
   - 在扫描循环内同时过滤 `status`、时间范围和搜索条件；
   - 命中后累积到 `limit`；
   - 默认最多扫描 `max(limit * 20, 1000)` 条，硬上限 10000 条。
2. 抽出批量 history 解析 helper：
   - 支持 Hash 格式；
   - 兼容旧 String 格式；
   - 忽略缺失或无法解析的数据。
3. 测试：
   - 构造 320 条较新的 pending；
   - 构造 1 条较旧 completed；
   - 验证 `GET /api/queue/{queue}/tasks?state=completed&limit=1` 能返回该 completed。
   - 构造 320 条较新的 completed，但它们不满足时间范围；
   - 构造 1 条较旧且满足时间范围的 completed；
   - 验证时间筛选不会被先截断逻辑遮挡。

## 非目标

- 不新增状态索引 ZSET。
- 不改变 Dashboard API 参数。
- 不保证超过扫描上限的极端稀疏状态一定完整返回；该场景后续可通过独立 status 索引解决。

## 验证

- `pytest tests/test_dashboard_api.py::test_dashboard_completed_tasks_scan_beyond_recent_pending -q`
- `pytest tests/test_dashboard_api.py -q`
- `pytest -q`
- `ruff check .`
- `mypy qtask_list cli dashboard remote_storage`
