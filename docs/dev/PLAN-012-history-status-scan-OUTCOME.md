# PLAN-012 结果: History 状态筛选扫描边界修正

## 完成内容

1. 复现并修复 `completed` / `failed` 状态稀疏漏查：
   - 原实现只扫描最近 `max(limit * 3, 300)` 条 history；
   - 当最近 320 条是 `pending`、较早有 `completed` 时，Dashboard completed 视图返回空；
   - 现在改为按批次扫描 history，直到命中 `limit` 或达到扫描上限。
2. 复现并修复时间筛选的先截断后过滤问题：
   - 原实现先取够 `limit` 条 completed/failed，再应用时间筛选；
   - 当较新的 completed 不满足时间范围、较旧 completed 满足时，会返回空；
   - 现在在扫描循环内同时应用状态、时间范围和搜索条件。
3. 优化 history 读取方式：
   - 新增 `_read_history_records()` 批量读取 helper；
   - 用 pipeline 批量获取 Redis 类型和任务内容；
   - 兼容 Hash 和旧 String 格式；
   - `_read_history()` 也复用批量读取，减少逐条 RTT。
4. 保留扫描边界：
   - 默认最多扫描 `max(limit * 20, 1000)` 条；
   - 硬上限 10000 条；
   - 不引入新的 Redis 状态索引，避免扩大存储模型。

## 涉及文件

| 文件 | 变更 |
|------|------|
| `qtask_list/admin.py` | completed/failed 状态读取改为有上限的分批扫描；时间筛选和搜索在扫描内执行；history 批量读取 |
| `tests/test_dashboard_api.py` | 增加状态稀疏和时间筛选遮挡两个回归测试 |
| `docs/dev/PLAN-012-history-status-scan.md` | 本轮计划 |

## 验证结果

- `pytest tests/test_dashboard_api.py::test_dashboard_completed_tasks_scan_beyond_recent_pending -q`：通过
- `pytest tests/test_dashboard_api.py::test_dashboard_completed_time_filter_scans_beyond_recent_completed -q`：通过
- `pytest tests/test_dashboard_api.py -q`：24 passed
- `pytest -q`：90 passed
- `ruff check .`：通过
- `mypy qtask_list cli dashboard remote_storage`：通过
- `git diff --check`：通过

## 剩余边界

超过 10000 条扫描上限且目标状态仍极端稀疏时，仍可能查不全。若后续业务确实需要在超大 history 中稳定查询 completed/failed/expired，应新增按状态或时间维度的二级索引，而不是继续扩大线性扫描。

## 后续建议

下一轮建议审查 `queue_stats()` 中 completed/failed/expired 统计的采样/外推策略。当前统计对超大 history 是近似值，适合概览，但可能在状态分布突变时误导运维判断。
