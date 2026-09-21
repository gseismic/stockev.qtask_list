# qtask_list 管理后台 UI 重设计 · 设计规范 V1

- 版本：v1（2026-09-21）
- 形态：React 组件化设计稿（规范文档 + HTML 原型 + React 组件树）
- 目录：`docs/ui-design/v1/`

```
docs/ui-design/v1/
├── README.md                ← 本文件：总纲（场景、页面清单、导航、React 组件树）
├── tokens.md                ← 设计 tokens（色彩/字阶/间距/圆角）
├── states.md                ← 状态矩阵与告警规则
├── pages/
│   ├── 01-overview.md       ← 总览（首页）
│   ├── 02-queues.md         ← 队列列表与详情
│   ├── 03-tasks.md          ← 任务浏览（全局任务视图）
│   ├── 04-task-detail.md    ← 任务详情（抽屉）
│   ├── 05-workers.md        ← Worker 监控
│   └── 06-alerts.md         ← 告警中心
└── prototype.html           ← 单文件高保真原型（自包含、可交互、含异常态切换）
```

## 1. 设计输入

> 为 **股票数据采集的运维/开发人员** 在
> **① 每日巡检系统健康**、**② 排查任务失败/堆积**、**③ 处理告警与死信、做重放/恢复等控制操作**
> 场景下设计 **Web 桌面端** 管理后台；品牌约束 **无**；内容密度 **高**；存量系统 **已勘察**（现有
> Jinja + 原生 JS 单页，左栏队列卡片 + 右侧任务表格 + 右侧操作面板，见
> `docs/design/dashboard-ux-20260622-overview.md`）。

所有设计稿页面均为 React 组件化视图；本目录设计稿以静态 HTML 原型表达视觉与交互，
组件边界按 §5 的 React 组件树标注，供后续正式 React 实现参考。

## 2. Top 场景 `[假设]`

| # | 场景 | 触发 | 频率 | 主页面 |
|---|------|------|------|--------|
| S1 | 巡检：一眼确认所有队列/Worker 是否健康 | 每天/工作时段多次打开 | 高 | 总览 |
| S2 | 排查：某个队列 DLQ 涨了/失败了，找出原因 | 告警触发后进入 | 高 | 队列详情 + 任务详情 |
| S3 | 控制：重放 DLQ、恢复卡死任务、改截止时间重放过期任务、清理队列 | 处理告警时 | 中 | 队列详情 / 任务详情 / 告警中心 |
| S4 | 观察：Worker 心跳、线程占用、Redis 内存 | 巡检附带 | 中 | Worker 监控 |

## 3. 页面清单

| 页面 | 路由 | 服务场景 | 频率 | 主行动点 |
|------|------|---------|------|---------|
| 总览 | `/` | S1 | 高 | —（状态确认型页面，无强 CTA） |
| 队列列表 | `/queues` | S1、S2 | 高 | 进入队列详情 |
| 队列详情 | `/queues/:ns:name` | S2、S3 | 高 | 任务表格筛选（P1），控制操作（P2/P3 收纳） |
| 任务浏览 | `/tasks` | S2 | 中 | 搜索/筛选任务 |
| 任务详情（抽屉） | 任务表格内嵌 | S2、S3 | 高 | 重放/删除等单任务操作 |
| Worker 监控 | `/workers` | S4 | 中 | 恢复失联 Worker 的任务 |
| 告警中心 | `/alerts` | S1、S3 | 高 | 逐条处理告警（跳转定位） |

与现状差异（改版要点）：

1. 现有单页"左队列卡 + 中表格 + 右操作面板"改为 **总览 + 队列详情 + 全局任务视图 + 告警中心** 四块信息架构，操作面板不再常驻，控制操作收纳到详情抽屉和队列"操作"菜单，减少误触。
2. 新增 **告警中心**：把 DLQ 堆积、失败率、任务过期、Worker 失联、Redis 内存超限等异常从"靠肉眼扫描卡片"变成主动推送的告警列表（可跳转定位、可标记处理）。现有数据里已有 `stale_worker`/`dlq`/`deadline_missed` 等信号，告警中心是基于这些信号的前端聚合展示。
3. 时间线组件统一展示任务生命周期（ready → processing → retry_wait → … → terminal），V2 的 `replay_of/replayed_by` 血缘在详情中可视化。
4. 全局任务搜索（task_id / action / payload）独立成页，队列详情页保留"本队列内"筛选。

## 4. 导航与闭环

```
                 ┌──────────┐
                 │  总览 /   │  全局健康卡 + 队列健康矩阵 + 告警摘要
                 └────┬─────┘
        ┌─────────────┼──────────────┐
        ▼             ▼              ▼
  ┌──────────┐  ┌──────────┐  ┌──────────┐
  │ 队列列表  │→│ 队列详情  │  │ 告警中心  │──(定位)──▶ 队列详情/任务详情
  └──────────┘  └────┬─────┘  └──────────┘
                     ▼
               任务表格 ──点击行──▶ 任务详情抽屉（可重放/删除/查看血缘）
```

- 左侧固定导航：总览 / 队列 / 任务 / Worker / 告警（告警项带未处理计数红点）。
- 任何告警卡片 → 队列详情并预置对应状态筛选（如 `state=dlq`）。
- 队列详情的任务行 → 详情抽屉，抽屉内完成 S3 的单任务控制，关闭即回表格，无页面跳转。

## 5. React 组件树（实现参考）

```
<App>
 ├─ <AppShell>                        # 左导航 + 顶栏（刷新/自动刷新/连接状态）
 │   ├─ <SideNav>                     # 总览/队列/任务/Worker/告警(+Badge)
 │   ├─ <TopBar>                      # namespace 选择、自动刷新、登录用户
 │   └─ <RouterOutlet>
 │       ├─ <OverviewPage>
 │       │   ├─ <HealthSummaryCard>   # 全局健康：队列数/DLQ 总量/失联 Worker/内存
 │       │   ├─ <QueueHealthMatrix>   # 队列健康矩阵（状态计数徽章，DLQ>0 红）
 │       │   └─ <AlertDigest>         # 最新告警 Top5 → /alerts
 │       ├─ <QueueListPage>
 │       │   └─ <QueueCard>…          # 与总览矩阵同数据源，卡片形态
 │       ├─ <QueueDetailPage>
 │       │   ├─ <QueueHeader>         # 名称、stats 徽章、诊断、操作菜单
 │       │   ├─ <StateTabs>           # ready/processing/retry/retry_wait/dlq/delay/…
 │       │   ├─ <TaskFilterBar>       # 搜索、时间范围、action、状态
 │       │   ├─ <TaskTable>           # 列：task_id/action/attempt/状态/时间
 │       │   │   └─ 行点击 → <TaskDrawer>
 │       │   ├─ <QueueDiagnostics>    # diagnose 结果面板
 │       │   └─ <DangerZone>          # 清理/删除（二次确认）
 │       ├─ <TasksPage>               # 全局任务视图：复用 TaskFilterBar/TaskTable
 │       ├─ <WorkersPage>
 │       │   ├─ <WorkerTable>         # 心跳、绑定队列、processing 计数、stale 标记
 │       │   └─ <RedisMemoryCard>     # INFO MEMORY + 阈值
 │       └─ <AlertsPage>
 │           └─ <AlertList> → <AlertItem>  # 严重度、来源队列、定位按钮、标记已处理
 ├─ <TaskDrawer>                      # 任务详情：payload/result、时间线、血缘、控制
 └─ <ConfirmDialog>                   # 危险操作二次确认
```

## 6. 质量门自查

- [x] S1 巡检首屏 = 总览页；S2 从告警/队列矩阵 1 次点击进入定位筛选
- [x] 每页主行动点唯一；危险操作全部二次确认并降级收纳
- [x] 状态矩阵覆盖空/加载/错误/极端内容（见 states.md）
- [x] 对比度 ≥ 4.5:1；正文对比 AA
- [x] 所有数值走 tokens（见 tokens.md）
- [x] 存量 API 能力映射（所有控制操作对应 `QueueAdmin` 已有方法，见 states.md §3）

## 7. 假设与开放问题

- `[假设]` 告警规则阈值：DLQ>0 即告警、失败率>5% 警告、Worker 失联即严重、Redis 内存超 `monitor_threshold_mb` 即严重——待实际运行校准。
- `[假设]` 告警"已处理"状态目前仅存前端 localStorage（单浏览器），如需多人协作应落 Redis。
- 开放问题：是否需要 RBAC（只读观察者 vs 管理员）？当前沿用现有登录会话，全员同权。
- 开放问题：总览矩阵在队列 >100 时是否需要按 namespace 分组折叠？（当前假设：按 namespace 分组。）
