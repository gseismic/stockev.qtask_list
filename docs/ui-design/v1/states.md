# 状态矩阵与告警规则

## 1. 每页状态矩阵

状态枚举：默认（有数据）/ 空状态 / 加载态 / 错误态 / 极端内容。

| 页面 | 默认 | 空状态 | 加载态 | 错误态 | 极端内容 |
|---|---|---|---|---|---|
| 总览 | 健康卡 + 队列矩阵 + 告警摘要 | —（总有数据：至少显示 0 队列引导） | KPI 与矩阵显示骨架屏 | 全局错误横幅 + 重试按钮，矩阵保留上次数据并置灰 | 队列 >100：按 namespace 折叠分组，默认展开有异常的组 |
| 队列列表 | 卡片网格 | "没有队列，去投递第一条任务" + 打开投递对话框按钮 | 骨架卡片 ×6 | 错误横幅 + 重试 | 同总览分组折叠 |
| 队列详情 | 状态 Tabs + 任务表格 | 所选状态 0 条：说明该状态含义 + 常见原因 + "切到 all"链接 | 表格骨架行 ×10 | 表格区错误 + 重试 | 1000+ 条：分页 50/页，显示总数与总页码 |
| 任务浏览 | 同队列详情，筛选栏增加队列维度 | 筛选无结果："没有匹配的任务" + 清空筛选按钮 | 同上 | 同上 | 超长 payload 截断 + "在抽屉中展开" |
| 任务详情抽屉 | payload/result + 时间线 + 血缘 | 无 result 时该节显示"任务尚无结果" | 抽屉骨架 | 加载失败：抽屉内错误 + 重试 | 大 payload（外存）：懒加载按钮"下载完整 payload" |
| Worker 监控 | Worker 表 + 内存卡 | "没有在线 Worker。参考 README 启动 Worker，确认后这里会出现心跳" | 表格骨架 | 错误横幅 | Worker >50：滚动 + 虚拟列表 |
| 告警中心 | 告警列表 | "🎉 当前没有告警" + 说明告警规则 | 列表骨架 | 错误横幅 | 告警 >99：折叠同队列同类告警为一条计数项 |

统一约定：

- 所有请求失败在页面顶部显示全局错误条（红色横幅，含"重试"按钮），不弹 toast 遮挡表格。
- 自动刷新（默认 5s，可关）在错误后自动降频为 30s，恢复后回升。
- 危险操作（清空/删除/强制恢复）一律 `<ConfirmDialog>`，需输入队列名确认（与现状 CLI `--force` 语义对齐）。

## 2. 任务详情时间线

数据源：任务记录的 `created_at` / `updated_at` / `attempt` / `outcome` / `location` / `replay_of` / `replayed_by`。

```
● 2026-09-21 10:00:05  投递      logical_key=quote:AAPL:20260921T1000
● 2026-09-21 10:00:07  开始执行  worker=fetch-3  attempt=1
● 2026-09-21 10:00:12  失败      ConnectionError → 30s 后重试
● 2026-09-21 10:00:42  重试执行  attempt=2
● 2026-09-21 10:00:44  完成 ✓
```

- 终态节点按 outcome 着色（completed=绿 / failed=红 / skipped·cancelled·deadline_missed=灰）。
- `replay_of` 显示"由任务 xxx 重放而来"链接；`replayed_by` 显示"已重放为 xxx"。
- 非终态任务显示"下次执行预估"（delay ZSET score）。

## 3. 进度口径与告警规则

### 3.1 进度口径（多队列盯盘，见 pages/01-overview.md）

- 剩余 = ready + processing + retry_wait + delay（`queue_stats`，现有能力）。
- 完成/1h、速率（条/min）= 前端对 `/api/queues` 的周期采样差值估算 `[假设]`；冷启动两个采样周期内显示"—"。
- ETA = 剩余 / 速率；速率=0 且剩余>0 判定"停滞"（danger）。
- "本轮期望总量"百分比分母：**开放问题**——若调度器/期望清单（如 reconciler JSONL、scheduler 每轮 universe）能提供每轮应完成数，增加接口 `/api/queue/{name}/progress` 后切换为"完成/期望"；V1 先用吞吐相对进度。

### 3.2 告警规则 `[假设阈值，待校准]`

数据全部来自现有 API 能力（`get_health` / `list_queues` / `queue_stats` / `list_workers` / `diagnose`），不要求新增后端。

| 规则 | 严重度 | 检测数据源 | 定位跳转 |
|---|---|---|---|
| 队列 DLQ > 0 | danger | `queue_stats.dlq` | 队列详情 `state=dlq` |
| 队列 failed 比例 > 5%（最近 50 条 history） | warning | `_history_stats` | 队列详情 `state=failed` |
| 队列 processing 存在 stale worker | danger | `diagnose.stale_workers` | 队列详情 + Worker 页 |
| 队列 ready 持续 > 阈值（默认 10000，10 分钟） | warning | `queue_stats.ready` | 队列详情 `state=ready` |
| 过期（deadline_missed/expired）任务出现 | warning | `list_expired` | 队列详情 `state=expired` |
| Worker 心跳超时（>2×心跳周期） | danger | `list_workers` | Worker 页 |
| Redis 内存 > `monitor_threshold_mb` | danger | `/api/health` | Worker 页内存卡 |

告警项操作：`定位`（跳转）、`标记已处理`（前端 localStorage，同规则复发重新出现）。

## 4. 权限与安全

- 沿用现有会话登录（`/api/login`），未登录 401 → 跳转 `/login`。
- 危险操作按钮在错误响应为 403 时置灰并提示权限不足（为将来 RBAC 预留）。
