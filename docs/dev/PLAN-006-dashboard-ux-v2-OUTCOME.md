# PLAN-006: Dashboard UX 综合优化 v2 - 实施结果

## 实施概要

对 7 个用户反馈问题全部完成修复，涉及 11 个文件变更。

## 变更清单

### 后端 (4 文件)

| 文件 | 变更内容 |
|------|---------|
| `qtask_list/admin.py` | `QueueState` 增加 `completed`/`failed`/`expired`；`list_tasks` 支持 4 个时间筛选参数；新增 `_read_history_by_status`/`_read_expired`/`list_expired`/`requeue_expired`/`_expired_count`/`_apply_time_filters`；`push_task` 透传 `expire_seconds` |
| `qtask_list/queue.py` | `push()` 新增 `expire_seconds` 参数，记录 `expires_at` 到 history |
| `qtask_list/history.py` | 无需改动（serialize_value 已处理 float） |
| `dashboard/main.py` | `PushTaskRequest` 增加 `expire_seconds`；`/api/queue/{name}/tasks` 增加 4 个时间参数；新增 `GET /api/queue/{name}/expired`、`POST /api/queue/{name}/requeue-expired`；`push_task` 透传 `expire_seconds` |

### 前端 (5 文件)

| 文件 | 变更内容 |
|------|---------|
| `dashboard/static/js/utils.js` | `states` 增加 `completed`/`failed`/`expired`；`stateLabel`/`stateCount` 对应扩展；`canRequeue` 支持 `expired` |
| `dashboard/static/js/api.js` | `queueTasks` 支持时间参数；`pushTask` 支持 `expireSeconds`；新增 `requeueExpired` |
| `dashboard/static/js/components.js` | `QueueList` namespace 哨兵值修复 (`null`=全部, `""`=无命名空间)；`StatsGrid` 增加"已过期"卡片；`QueueActions` 增加"放回过期"按钮；`TaskTable` 增加发布时间/完成时间列；`SystemBanner` 增加加载状态；`PushTaskForm` 增加过期秒数输入；`GlobalOverview` 增加过期统计；"只看当前"→"有活动的队列" |
| `dashboard/static/js/app.js` | `namespaceFilter` 默认值 `""`→`null`；新增 `expireSeconds` state；`requeueExpired` handler；`PushTaskForm` 透传 `expireSeconds`；`QueueActions` 透传 `onRequeueExpired`；`isLoading` 状态驱动 SystemBanner |
| `dashboard/static/css/app.css` | 新增 `.system-banner.loading` 样式 |

### 测试 (1 文件)

| 文件 | 变更内容 |
|------|---------|
| `tests/test_dashboard_api.py` | +`import time`；新增 6 个测试：`test_dashboard_push_with_expire`、`test_dashboard_list_expired`、`test_dashboard_requeue_expired`、`test_dashboard_queue_stats_includes_expired`、`test_dashboard_list_completed_tasks`、`test_dashboard_time_filter` |

## 验证结果

- **LSP diagnostics**: 全部 clean (0 errors, 0 warnings)
- **单元测试**: 78/78 passed (72 已有 + 6 新增)
- **覆盖范围**:
  - PushTaskRequest.expire_seconds 序列化通过
  - 过期任务列出/放回通过
  - queue_stats 包含 expired 计数通过
  - completed 状态过滤通过
  - 时间范围筛选通过

## 各问题对应修复

| # | 问题 | 修复方式 |
|---|------|---------|
| 1 | 加载慢/空白 | `_expired_count` 限制扫描 200 条；`SystemBanner` 显示"正在加载…"骨架；`isLoading` state 驱动 |
| 2 | 无命名空间筛选冲突 | `namespaceFilter` 使用 `null` 哨兵值区分"全部"和"无命名空间" |
| 3 | 标签页缺已完成 | states 增加 `completed`/`failed` 独立 tab；后端 `_read_history_by_status` 按 status 过滤 |
| 4 | 缺时间列/筛选 | 表格增加"发布时间""完成时间"列；API 支持 `created_after/before`、`completed_after/before` |
| 5 | "只看当前"歧义 | 重命名为"有活动的队列" |
| 6 | 缺过期机制 | `push()` +`expire_seconds`；新增 `expired` 状态、"放回过期"按钮；`requeue_expired` API |
| 7 | 布局优化 | `GlobalOverview` 含过期统计；`StatsGrid` 11 卡片含"已过期"；`SystemBanner` 加载态 |
