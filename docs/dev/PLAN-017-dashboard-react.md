# PLAN-017：实施 React 版管理后台（UI 设计稿 V1 落地）

- 日期：2026-09-21
- 依据：`docs/ui-design/v1/`（README 总纲、tokens.md、states.md、pages/01~06）、
  交接文档 `docs/HANDOFF.md` §5.2（建议编号 PLAN-017）
- 目标：按设计稿 V1 实施新管理后台，替换旧 Jinja + 原生 JS 单页；FastAPI 后端增量复用。

## 1. 范围

新信息架构（设计稿 §3）：总览 `/`、队列列表 `/queues`、队列详情 `/queues/:name`、
任务浏览 `/tasks`、Worker 监控 `/workers`、告警中心 `/alerts`、教程 `/guide`，
任务详情抽屉内嵌于任务表格。危险操作全部二次确认（输入队列名）。

## 2. 技术决策（含设计稿开放问题的落定）

| 事项 | 决策 |
|---|---|
| 前端栈 | Vite + React 18 + TypeScript + react-router-dom；无 UI 框架，样式按 tokens.md 手写 CSS |
| 目录 | 新建 `frontend/`（源码 + 构建），构建产物输出到 `dashboard/static/spa/` **并提交入库**，保持 `uvicorn dashboard.main:app` 零 Node 可用（与现状一致） |
| 旧 UI | 退役删除：`dashboard/templates/index.html`、`dashboard/static/js/`、`dashboard/static/css/`；`templates/login.html` 保留（服务端登录页继续复用会话逻辑） |
| SPA 路由托管 | FastAPI 增加非 `/api`、非 `/login`、非 `/static` 的 catch-all：命中 `static/spa` 内静态文件否则回退 `spa/index.html`；`/` 保留未登录跳转 `/login` |
| 进度口径 | V1 吞吐相对进度：进度% = 完成/1h ÷ (完成/1h + 剩余)，剩余 = ready+processing+retry_wait+delay（states.md §3.1）；"本轮期望总量"接口不做（开放问题留待调度器接入） |
| 速率/完成·1h | 前端对 `/api/queues` 的周期采样差值估算（冷启动 ~2 个采样周期显示"—"）， sampler 保留 1h 窗口 |
| 告警引擎 | 前端聚合 `/api/queues`、`/api/workers`、`/api/health`：DLQ>0、失败率>5%、stale worker、ready>10000 持续 10min、deadline_missed>0、Redis 内存超限；阈值沿用设计稿 `[假设]` |
| 失败率口径偏差 | 设计稿按"最近 50 条 history"；现有统计只有累计计数，V1 用累计 failed/(failed+completed) 近似，在告警规则说明中标注 |
| 告警已处理 | localStorage（单浏览器），同规则复发（condition false→true）重新出现 |
| 任务分页 | 现有 `list_tasks` 无 offset/total，V1 用"加载更多"（50→500 递增），总数徽章取自 `queue_stats` 对应状态计数 |
| 全局任务搜索 | 复用 `/api/tasks`；task_id 精确命中（`/api/task/{id}`）直接打开抽屉 |
| 教程文案 | 独立常量文件（pages/06-guide.md 维护约定），内容按该文档 §内容清单撰写 |
| 新增后端接口 | 仅 2 个：SPA catch-all 托管、`POST /api/queue/{name}/clean-history`（`admin.clean_history` 已有能力，设计稿操作菜单要求） |

## 3. 实施步骤

1. 后端：`dashboard/main.py` 增加 SPA 托管与 clean-history；`pyproject.toml` package-data
   增加 `static/spa/**`；删除旧 UI 文件。
2. 前端脚手架：`frontend/`（package.json、vite.config.ts、tsconfig、index.html）。
3. 前端实现：
   - 基础：`api.ts`（401→/login）、`types.ts`、`usePolling`（5s 默认，错误降频 30s）、
     `sampler.ts`（速率/完成1h/ETA/停滞）、`alerts.ts`（规则引擎+localStorage）、
     `theme.tsx`（白天默认/黑夜，localStorage+prefers-color-scheme）。
   - 组件：StateBadge、ProgressBar（三段式）、KpiCard、Skeleton、EmptyState、
     ErrorBanner、ConfirmDialog（输入队列名）、Drawer、OpsMenu、TaskTable、
     TaskFilterBar、TaskDrawer、PushTaskDialog。
   - 页面：Overview、Queues、QueueDetail、Tasks、Workers、Alerts、Guide（8 节教程）。
   - AppShell：左侧导航（告警未处理数红点）+ 顶栏（自动刷新、主题切换、用户/登出）。
4. 构建：`npm run build`（tsc + vite），产物入 `dashboard/static/spa/`。
5. 验证：
   - `python -m pytest tests/ -q`、`ruff`、`mypy`、`compileall examples`；
   - 前端 `npm run build` 零错误；
   - 本地 Redis 种子数据 + chrome-devtools 逐页验证（默认/空/异常、告警定位、
     抽屉操作、二次确认、双主题、刷新降频），结束后清理测试 namespace。
6. 产出：`PLAN-017-dashboard-react-OUTCOME.md`、INDEX 追加、中文 commit + push。

## 4. 风险与回退

- npm 不可用：降级为 CDN React 方案（无构建），结构与组件树不变。
- 产物提交体积：仅 hash 命名的 js/css/字体，无字体依赖，预计 <300KB。
- 旧 UI 删除后如有遗漏入口：`git revert` 可整体回退本次前端替换。
