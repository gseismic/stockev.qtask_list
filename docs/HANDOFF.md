# 交接文档（HANDOFF）

- 更新时间：2026-09-21 17:10（Asia/Shanghai）
- 当前基线：PLAN-017 React 管理后台已实施（见 docs/dev/INDEX.md）
- 交接范围：qtask_list V2 核心、管理后台、前端、测试、文档、stockev 示例，以及
  管理后台 UI 重设计设计稿（本轮新工作）
- 工作树保护：`AGENTS.md` 是用户已有未提交修改，始终不修改、不暂存、不提交

## 1. 项目背景

qtask_list 是只依赖 Redis 的分布式任务队列库，面向股票数据采集的批量回补、7×24
定时抓取和动态新闻列表。库负责可靠执行、状态转换和重试；调度策略、幂等落库和业务
持久化由使用方负责。

V2（版本 `0.2.0`）将业务 payload 与运行元数据分离：`TaskSpec` 描述 action、payload、
身份键、调度和截止时间；V2 信封支持 inline/zstd/external；Redis Lua 状态机维护
attempt、deadline、outcome、location、replay 血缘和 live identity。V1 信封、旧 retry
List、`expires_at`/`_retry` 仍可双读或安全降级。

管理侧由三部分复用同一套 `QueueAdmin` 语义：Typer CLI（`qtask_list/cli/__main__.py`）、
FastAPI Dashboard（`qtask_list/dashboard/`，React SPA，见 PLAN-017）、Python SDK。
（PLAN-019 起仓库为单包结构，cli/dashboard/remote_storage/frontend 均在 `qtask_list/` 内，前端用 pnpm。）

## 2. 当前已完成

### 2.1 库与后端（截至 PLAN-016，详见旧索引）

1. V2 核心、入口统一、状态机、Dashboard/前端交付收尾：见
   `docs/dev/PLAN-015-qtask-v2.md` 与 `-OUTCOME.md`。
2. 旧 V1 DLQ/历史双读 fallback、`retry_wait` 统一由注入客户端计算、Dashboard 4xx、
   Worker 优雅 drain、测试迁移 V2 语义：见
   `docs/dev/PLAN-016-v2-review-remediation.md` 与 `-OUTCOME.md`。
3. examples 已重组为 `01~09` 分层学习路径并全面 review（提交 `7dfec6b`、`43c3cba`）：
   - `examples/stockev/scheduler.py`：确定性 5 分钟 slot、早晚 universe、历史分区；
   - `examples/stockev/news_discover.py`：`TaskResult.emissions` 新闻 fan-out；
   - `examples/stockev/reconciler.py`：JSONL 期望清单的幂等补齐。
4. 计划-结果索引：`docs/dev/INDEX.md`（从 PLAN-013 起维护，更早按文件名追溯）。

### 2.2 本轮新工作：管理后台 UI 重设计 V1（设计稿，未动代码）

用户要求重新设计管理后台，目标：方便查看/筛选/看警告/控制；随后补充了两条需求：
多 namespace、几十到上百条抓取任务（= 队列）同时跑的**进度盯盘**；以及新增
**教程**栏目。三轮交付均为设计文档 + 单文件 React 原型（提交 `8aa9de3`、`4d4e532`、
`9c430dd`），**没有修改任何 dashboard/ 生产代码**。

设计稿位置与结构（全部在 `docs/ui-design/v1/`）：

- `README.md`：设计输入卡、Top 场景（S0 盯盘/S1 巡检/S2 排查/S3 控制/S4 Worker/S5 学习）、
  页面清单、导航闭环、React 组件树、质量门自查、开放问题。
- `tokens.md`：白天（默认）/黑夜双主题 tokens，同一组语义变量名。
- `states.md`：每页状态矩阵（默认/空/加载/错误/极端）、任务时间线设计、
  §3.1 进度口径、§3.2 告警规则（基于现有 API，阈值标记 `[假设]`）。
- `pages/01~06-*.md`：逐页设计——总览（进度总览）、队列列表+详情、任务浏览+详情抽屉、
  Worker 监控/告警中心、教程（说人话概念手册，8 节）。
- `prototype.html`：单文件高保真原型（CDN React，无构建依赖），已在浏览器逐页验证
  渲染与交互，含默认/加载/错误/空态切换器。

设计要点（与现状差异）：

1. 信息架构从"单页三栏"改为 总览 / 队列列表 / 队列详情 / 全局任务视图 / 告警中心 /
   Worker / 教程 七块；危险操作收纳 + 二次确认。
2. **进度总览**（S0 核心需求）：KPI（活跃队列/总剩余/合计速率/异常队列，可下钻）、
   队列进度矩阵按 namespace 分组折叠，每行三段式进度条（完成/剩余/DLQ）+ 剩余 +
   完成/1h + 速率 + ETA + 停滞判定。
3. **告警中心**：DLQ 堆积、失败率、stale worker、Redis 内存等信号聚合，可定位可标记。
4. **教程页**：说人话概念手册（任务/队列关系、状态流转徽章图、操作速查表）。
5. **双主题**：默认白天，可切黑夜，localStorage 记忆。

## 3. 待用户确认的口径问题（实施前必须澄清）

1. **进度分母**：V1 百分比是"最近 1h 完成 / (完成+剩余)"的吞吐相对进度。若调度器
   能提供"本轮应完成总量"（如 scheduler 每轮 universe 数、reconciler 期望清单），
   应改为"完成/期望"绝对进度，需新增后端接口（见 `states.md` §3.1）。
2. **速率来源**：速率/ETA 为前端采样估算 `[假设]`；如需精确吞吐与历史曲线，需后端
   增加 throughput 聚合接口。
3. 告警阈值（DLQ>0、失败率 5%、ready>10000 持续 10min）均为 `[假设]`，待运行校准。

## 4. 验证基线

```bash
python -m pytest tests/ -q
python -m ruff check .
python -m mypy qtask_list
python -m compileall -q examples
# 前端（PLAN-017 起）：源码 qtask_list/frontend/，构建产物提交在 qtask_list/dashboard/static/spa/（PLAN-019 起用 pnpm）
cd qtask_list/frontend && pnpm install && pnpm build
```

Redis 默认连接 `redis://localhost:6379/0`。

## 5. 后续可选方向（按优先级）

1. **评审并定稿 UI 设计稿 V1**：用户确认口径问题（§3）与页面结构后，冻结设计稿
   （可升版本号 v1.1/v2）。
2. **实施 React 版管理后台**：按仓库约定新建 `docs/dev/PLAN-017-dashboard-react.md`
   （编号续 `docs/dev/INDEX.md`），技术选型 `[假设]`：Vite + React + TS；服务端继续用
   `dashboard/main.py`（FastAPI），按 `states.md` 补少量接口（进度采样/期望总量）。
   设计稿→计划→实施→OUTCOME→INDEX 追加→提交推送。
3. 生产 Redis 上压测 `QueueAdmin.queue_names()` SCAN；RemoteStorage orphan cleanup、
   DLQ 告警回调接入实际部署；scheduler 交易日历/分区完善；consistency report、
   lease、archive leader 故障注入测试（沿袭旧交接）。

## 6. 关键文档索引

- 设计定稿：`docs/design/qtask-overall-20260920-v2.md`（V2 核心）、
  `docs/design/task-identity-20260920-logical-key.md`（身份去重）、
  `docs/design/dashboard-ux-20260622-overview.md`（旧 Dashboard UX 改版史）
- 本轮 UI 设计稿：`docs/ui-design/v1/`（README 为总纲，prototype.html 可直接打开）
- 计划-结果：`docs/dev/INDEX.md`（PLAN-013~016）
- API/运维语义：`README.md`、`skills/qtask-list-usage/SKILL.md`

## 7. 给 0 背景 agent 的提示

- 本仓库工作流：读 `AGENTS.md` → 计划文件 `PLAN-{n}-{tag}.md` → 实施 → 结果文件
  `-OUTCOME.md` → 追加 `docs/dev/INDEX.md` → 中文 commit（列明文件）→ 立刻 push；
  `AGENTS.md` 永不提交。
- 现有 Dashboard 是 Jinja + 原生 JS（`dashboard/static/js/{app,components,api,utils}.js`）；
  UI 重设计 V1 是**新架构设计稿**，不要直接在旧 JS 上续写，实施时按设计稿新起 React 前端，
  FastAPI 后端可增量复用（新增接口见 `states.md` API 缺口标注）。
- 原型 `prototype.html` 用 CDN React + babel-standalone，双主题已验证；交互 bug
  （ctx 漏传 drawer/dialog 字段）已修复，可作为组件行为的参照。
