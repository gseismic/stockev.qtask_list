# PLAN-017 结果：React 版管理后台实施完成

- 日期：2026-09-21
- 计划文件：`docs/dev/PLAN-017-dashboard-react.md`
- 状态：完成

## 交付内容

### 1. 前端（新增 `frontend/`，Vite + React 18 + TypeScript）

- 信息架构按设计稿 V1：总览 `/`、队列列表 `/queues`、队列详情 `/queues/:name`、
  任务浏览 `/tasks`、Worker `/workers`、告警 `/alerts`、教程 `/guide`，
  任务详情为内嵌抽屉。路由 react-router-dom。
- 组件：StateBadge（状态色映射，processing 呼吸动画）、三段式 ProgressBar、
  KpiCard（可下钻）、Skeleton/EmptyState/ErrorBanner、ConfirmDialog（危险操作需
  输入队列名）、OpsMenu（危险项红色置底）、TaskTable/TaskFilterBar、TaskDrawer、
  PushTaskDialog。
- 采样器 `sampler.ts`：对 `/api/queues` 周期采样差值估算 完成/1h、速率、ETA、
  停滞判定；进度% = 完成/1h ÷ (完成/1h + 剩余)；冷启动显示"—"。
- 告警引擎 `alerts.ts`：DLQ>0、累计失败率>5%（≥10 样本）、stale worker、
  ready>10000 持续 10min、deadline_missed>0、Worker 心跳超时、Redis 内存超限；
  规则解除自动消失；"标记已处理"存 localStorage，同规则复发重新出现；导航栏
  红点计数。
- 双主题 `theme.tsx`：默认跟随 prefers-color-scheme（无偏好则白天），手动切换
  localStorage 记忆；tokens 按 `docs/ui-design/v1/tokens.md`。
- 轮询 `hooks.ts`：默认 5s，可切 15s/30s/关，错误后自动降频 30s 并在恢复后回升；
  页面隐藏时跳过请求。
- 教程文案独立常量 `guideText.ts`（按设计稿 §06 的 8 节清单）。

### 2. 后端（`dashboard/main.py` 增量）

- SPA 托管：`/assets` 静态挂载 + 非 API catch-all（命中 `static/spa` 文件否则回退
  `index.html`）；`/` 保留未登录 307 → `/login`；`/login` 继续用服务端登录页
  （新拆分 `static/css/login.css`，删除旧 `app.css`）。
- 新增 `POST /api/queue/{name}/clean-history?ttl_days=`（复用 `admin.clean_history`）。
- 旧 UI 退役删除：`templates/index.html`、`static/js/`、`static/css/app.css`。
- `pyproject.toml` package-data 增加 `static/spa/*`、`static/spa/assets/*`
  （构建产物入库，`uvicorn dashboard.main:app` 零 Node 可用）。

### 3. 相对设计稿的口径落定（均已在 UI 中标注）

- 进度口径：吞吐相对进度（完成/1h 采样差值），"本轮期望总量"分母未做（开放问题
  留待调度器接入）。
- 失败率告警：累计 failed/(failed+completed)，≥10 样本才判定；设计稿的
  "最近 50 条 history"口径需后端新增时间窗统计，暂以累计近似并写入规则说明。
- 任务分页：现有 `list_tasks` 无 offset/total，采用"加载更多"（50→500），
  状态总数徽章取自 `queue_stats`。
- ready>10000 持续 10min：由前端引擎记录首次触发时间实现。

## 验证

- `python -m pytest tests/ -q`：118 passed。
- `ruff check .`、`mypy qtask_list cli dashboard remote_storage`、
  `compileall examples`：全部通过。
- `npm run build`（tsc --noEmit + vite build）：零错误，产物
  `dashboard/static/spa/`（gzip 后 JS ≈74.5KB）。
- 浏览器端到端（本地 Redis 种子数据，qa:* namespace，验证后已清理）：
  - 总览：KPI/告警摘要/按 namespace 折叠矩阵/异常行置顶/停滞标红/采样冷启动"—"；
  - 队列列表：筛选（namespace/全部/进行中/仅异常/搜索）+ 卡片 + 操作菜单；
  - 队列详情：URL `?state=dlq` 直达、状态 Tabs 计数徽章、任务表格、诊断弹窗、
    投递测试任务、危险操作输入队列名确认；
  - 任务抽屉：时间线、payload 懒加载（DLQ 消息解码成功）、血缘"已重放为"、
    从 DLQ 重入队（原地刷新，stats 同步变化）、按状态显隐按钮；
  - Worker：KPI/失联恢复（确认后 2 个孤儿任务回 ready）/Redis 内存卡；
  - 告警中心：规则说明表、定位、标记已处理（复发语义）、已处理 tab；
  - 教程：TOC 锚点 + 状态徽章流程图；双主题切换与记忆；
  - 登录：`QTASK_DASHBOARD_PASSWORD` 启用后 /login 渲染、未登录 `/` 307、
    登录后 API 与页面正常。
- 验证脚本为临时脚本（`/tmp/opencode/seed_dashboard.py`），未入库；测试
  namespace `qa:*` 已全部删除。

## 遗留与后续

- 全局任务搜索 `/api/tasks` 逐队列扫描，队列多时偏慢；后续可加服务端聚合。
- 告警阈值与失败率时间窗口径待运行数据校准（`[假设]` 沿袭设计稿）。
- `docs/HANDOFF.md` 的旧验证基线命令（`node --check dashboard/static/js`）已失效，
  前端检查改为 `npm run build`（见 HANDOFF 更新）。
