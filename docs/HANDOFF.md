# 交接文档（HANDOFF）

- 更新时间：2026-09-20 17:40（Asia/Shanghai）
- 基线 commit：`bb46a4d`（PLAN-015 第一批 V2 核心 WIP 快照，已 push）
- 交接人：opencode（GLM，会话任务：管理后台 + 前端）
- 阅读顺序建议：第 1 节 → 第 2 节现状 → 第 3 节未完成清单 → 第 5 节决策记录

## 1. 项目背景（零上下文可读）

**qtask_list** 是基于 Redis List 的分布式任务队列库（`pip install -e .`，CLI 命令 `qtask`，FastAPI + React Dashboard）。V1 机制：`BRPOPLPUSH` 可靠消费 + ready/processing/retry/dlq/delay 多容器 + per-worker heartbeat stale-safe recovery。

**最终用途**：股票数据采集系统的执行队列底座（批量回补、7×24 定时抓取、动态增长抓取列表）。库定位：**只做可靠执行队列**，调度/幂等落库/持久化是使用方职责。

**V2 大版本重构（当前主线）**：设计稿 `docs/design/qtask-overall-20260920-v2.md`（1302 行，含 §21 验收标准）要求：

- `TaskSpec`/`EnqueueResult` 结构化投递；业务 payload 与运行元数据彻底分离（V2 信封 version=2 + inline/zstd/external descriptor，V1 双读兼容）；
- Redis Lua 原子状态机（`qtask_list/state.py`）：live identity（哈希化 dedup owner，无普通 TTL，终态按 `dedup_until` 精确保留）、attempt 在 begin_attempt 精确计数、deadline/supersede 命中不消耗 attempt、终态不可变；
- DLQ replay 一律新 task_id（`replay_of`/`replayed_by` 血缘），终态位置（outcome）与消息位置（location）正交；
- keyed concurrency lease、latest-only supersede、`TaskResult.emissions` fan-out、maintenance leader、consistency report。

**环境与验证基线**：

- Redis `redis://localhost:6379/0`（当前 db0 里是演示数据 `dashdemo:*`，非真实业务数据）
- 测试基线（bb46a4d）：**78 passed / 36 failed**——全部是旧 V1 断言未按 V2 语义更新（见 3.3 分类），核心状态机本身经浏览器端到端验证工作正常
- mypy：1 错误（`qtask_list/queue.py:1441` external_retention_risk 分支 `float|None` 比较）；ruff：2 错误（`tests/manual/verify_review_claims.py` E702）
- Dashboard：`python -m uvicorn dashboard.main:app --host 127.0.0.1 --port 8765`（本会话以 nohup 在跑，日志 /tmp/opencode/dashboard.log）

**关键文档索引**：

| 文档 | 内容 |
|---|---|
| `docs/design/qtask-overall-20260920-v2.md` | V2 整体设计定稿（实施依据，含 §21 验收标准） |
| `docs/dev/PLAN-015-qtask-v2.md` | V2 实施计划（A–D 阶段、验证矩阵、Review 要点） |
| `docs/dev/INDEX.md` | 计划-结果历史索引（PLAN-015 尚未登记 OUTCOME） |
| `docs/HANDOFF.md` | 本文件 |
| `README.md` | V1 时代使用文档（**未按 V2 更新**） |
| `skills/qtask-list-usage/SKILL.md` | 旧 API 使用 skill（**未同步**；`~/.agents/skills/qtask-usage`、`~/.agents/skills/pai-qtask-list-codexskill-oc` 仓库外副本同样过时） |

## 2. 当前状态

### 2.1 PLAN-015 第一批快照（bb46a4d，codex 干到一半）

codex 会话 01a0bda6-545f-74a3-aeb0-e5435bc2b1d0 按设计稿实施了阶段 A–D 的核心代码后中断（中断点：pytest 出现大量回归、正要按 V2 语义更新测试）。快照内容：

- 新增：`qtask_list/{models,clock,errors,envelope,state}.py`
- 重写：`queue.py`（V2 原子状态机 + push/push_batch 兼容层）、`worker.py`、`history.py`、`admin.py`、`storage.py`、`archiver.py`
- 入口：CLI（V2 字段 + 危险操作确认）、Dashboard 后端（`dashboard/main.py` TaskSpec 完整投递/confirm 参数）、前端 JS 大部分已改
- pyproject 版本调整；`AGENTS.md` 是用户自有改动（历次 commit 均刻意排除，继续排除）

### 2.2 本会话工作（用户指令：**只做管理后台和前端，Python 部分不动，除非修 bug**）

已完成的验证（浏览器 + Redis 对账，结论：UI 数据与后端一致，核心状态机语义正确）：

1. 清理 db0 测试残留，用 SDK 种入 `dashdemo:fetch` / `dashdemo:ingest` 演示数据（覆盖 ready/processing/delay/retry_wait/dlq/completed/skipped/cancelled/supersede/lease/zstd 各形态）；
2. **replay 闭环验证 ✓**：死信任务点「重试」→ 新 task_id 进 ready、DLQ 清空、原记录 `replayed_by` 回填、新实例 `replay_of`、toast 展示新 id；
3. supersede 验证 ✓：v1 任务被领取时因 v2 已存在而正确 cancelled（superseded）。

已修的前端 bug（**改动在工作树未提交**，见第 3.0 节）：

1. `utils.js`：新增 `taskFailure()`（从 `_raw.last_error` / reason 字段提取失败原因）与 `descriptorPayload()`（从 payload_descriptor 还原 inline data / zstd / external 占位）；`canRequeue()` 去掉 `retry_wait`（后端对 retry_wait 移动返回 moved=0 → 404，不提供入口）；`stateCount(retry_wait)` 回退到 `stats.retry`（重试家族计数）；
2. `components.js`：TaskTable payload 列改用 `descriptorPayload()`（V2 历史行不再显示 descriptor 原始 JSON）；TaskDrawer 重写——zstd/external descriptor 自动调用 resolve API 还原 payload、V2 历史任务 payload 缺失时自动加载、新增红色「失败原因 code + reason」高亮块、「任务详情」扩充 V2 可读字段（attempt/max_attempts/reason/logical_key/scheduled_for/deadline/dedup_until/trace/replay 血缘等），其余仍收进「内部元数据」；QueueList 的 outcome 行补 `⊘已取消 / ↷已跳过 / ⏰错过截止` 指示（>0 时显示）；
3. `app.js`：`pushOptions.action` 默认值由 `"example"` 改为 `""`（原默认会静默覆盖 payload.action）；`pushTask()` 提交前预校验「高级选项 action 或 payload.action 至少一个」，否则 toast 报错（原先会打到后端 500）；
4. `templates/index.html`：默认 payload 示例改为含 action 字段；
5. `app.css`：新增 `.failure-block/.failure-title/.failure-reason` 样式。

`node --check` 四个 JS 全部通过；表格 payload、已完成行、失败原因块、zstd drawer 均已在浏览器回归通过。

## 3. 尚未完成的事项

### 3.0 立即要做（本会话被交接文档打断）

1. **提交前端修复**：工作树有 4 个已改文件（`dashboard/static/js/{app,components,utils,api}.js`——api.js 本次未改但保持过检、`dashboard/static/css/app.css`）+ `dashboard/templates/index.html`。node --check 已过、核心路径已浏览器验证。按小步提交习惯单独 commit（中文消息，注明前端适配 V2，不提交 AGENTS.md），然后 push。
2. **前端回归收尾**（改完后浏览器再过一遍）：投递表单三条路径（payload.action / 显式 action / 都缺失时 toast）、ALLOW_NEW 确认流、危险操作（清空/释放身份/强恢复）、删除队列按钮（仅空队列显示）、错过截止视图、auth 登录页（QTASK_DASHBOARD_USER/PASSWORD 环境变量路径）、dashboard 首屏窄视口（现依赖 .table-wrap overflow-x 滚动，可接受）。
3. 已知小瑕疵（可选修）：retry_wait tab 内行的徽章显示「延迟」——后端 `_read_delay` 复用 state=delay，前端可在 state=retry_wait 时改标注；演示数据可再次重建（见第 5 节脚本思路）。

### 3.1 Python 部分等用户解禁后处理（当前禁改，除非修 bug）

测试失败分类（36 个，均为旧 V1 断言 vs V2 新语义，改法参考 `docs/dev/PLAN-015-qtask-v2.md` §5 验证矩阵）：

- **retry List → delay ZSET**（V2 fail 一律进 delay，`retry_backoff_base=0` 时 run_at=now）：`test_queue.test_fail_and_retry`、`test_worker.test_worker_exception_handling`；
- **payload `_retry` 字段取消**（attempt 在信封头/任务记录）：`test_backoff_exhaustion_reaches_dlq`、`test_requeue_dlq_*`；
- **dedup owner 语义变化**（哈希化 owner hash、live 不设 TTL、终态按 dedup_until 精确保留或删除；`push(dedup_ttl)` → dedup_until 绝对时刻，不再默认跟随 expire*2）：`test_push_dedup_same_key`、`test_push_dedup_ttl_default_follows_expire`；
- **deadline 从任务记录读**（BEGIN_ATTEMPT 读 HGET start_deadline_at，不再读消息 expires_at）：`test_expired_task_skipped_at_pop`（应改任务记录而非消息）；
- **历史 TTL 语义**（idx key 无 TTL；live 记录 persist、终态+无 operational message 才 EXPIRE；clean_expired 跳过 live/operational 记录）：`test_ttl`、`test_clean_expired`、CLI clean-history 两例（fixture 需造 terminal+operational_message=0 记录）；
- **history_mode=minimal 仍写任务记录**（ENQUEUE_V2_LUA 写最小字段集）：`test_record_history_false_skips_history`；
- **requeue/replay 新 task_id**：CLI `test_requeue_moves_dlq_to_ready`（断言应改为新 id + 原记录保持 failed）、`test_requeue_dlq_keep_retry`（reset_retry=False 仅发 DeprecationWarning）；
- **CLI recover --force-active 需要显式 `--yes`**（新确认约束）：`test_recover_skips_active_worker_processing_by_default`；
- **Dashboard expired 视图重构**（deadline_missed 从 operational 容器派生、requeue-expired 必须显式 `start_deadline_at`、对有消息任务走 cancel+replay）：`test_dashboard_api.py` 的 12 个 expired/requeue/delete 相关用例需按新语义重写；`test_dashboard_delete_queue` 需带 `confirm=true`；
- `test_integration` 空/大 payload：V2 `push()` 要求 payload 必须含 action（应改测试数据 + 补一条显式 ValueError 断言）；
- `test_archiver_manual.test_archiver`：需按 V2 归档条件（仅终态且无 operational message）更新。

其它工程债：

- mypy `queue.py:1441`：`retain_until not in (None, 0)` → 改为 `isinstance(retain_until, float) and retain_until > 0 and ...`（一行修复，属 bug 范畴，可请示后先修）；
- ruff `tests/manual/verify_review_claims.py:49,50` E702 分号 → 拆行；该脚本整体按 V2 假设重写（其 4 条论断全部基于 V1 行为）；
- PLAN-015 尾段工作：补 V2 新覆盖测试（身份/dedup 边界/ALLOW_NEW 血缘/原子性/lease/supersede/emissions/leader）、README+examples+skill+版本 0.2.0、`PLAN-015-qtask-v2-OUTCOME.md`、INDEX 登记、全量 pytest/ruff/mypy 绿、提交 push。

### 3.2 中期方向（来自旧交接与评审，实施前先读 PLAN-015）

- examples/stockev 按 V2 重写（scheduler、5 分钟 slot、新闻 fan-out、Reconciler）；
- DLQ 告警回调（Worker 已有 `alert_callback`/`dlq_alert_count`/`dlq_alert_age`，缺端到端使用示例）；
- skill 三副本同步 V2 语义；
- 队列发现 SCAN 性能、RemoteStorage TTL 对齐等旧债见 INDEX 历史。

## 4. 避坑记录（本会话实测结论，防止重复踩坑）

1. **UI 数据是否正确要用 Redis 对账再下结论**：本会话两次「疑似 UI bug」最终都是演示数据与预期不符（seed 脚本 pop 顺序与变量名错位；a11y 快照读到刷新中间态）。对账方法：直接 dump 各容器消息的 task_id/action/payload 与表格行比对。
2. `_read_deadline_missed` 只扫 operational 容器（ready/retry/delay）——纯历史记录（无消息）不会出现在「错过截止」视图，requeue-expired 对无消息任务返回 moved=0 + note，这是设计决定（PLAN 阶段 A 第 7 条）。
3. `retry_wait` 视图行来自 delay ZSET（delay_reason=retry），`_state` 仍是 delay，后端不支持手动提前。
4. V2 `push()` 兼容层：delay→not_before_at、expire→start_deadline_at、dedup_ttl→dedup_until；`force=True`→ALLOW_NEW。
5. `pop_claim_no_wait` 遇 superseded/cancelled 会跳过继续取下一条（返回的 claim 不一定是你刚 enqueue 的那个任务）。

## 5. 给接手者的快速上手

```bash
# Dashboard（若未在跑）
python -m uvicorn dashboard.main:app --host 127.0.0.1 --port 8765 &

# 演示数据已种好（dashdemo 命名空间）；重新造数据用 SDK：
# SmartQueue("redis://localhost:6379/0", "fetch", namespace="dashdemo")
#   .enqueue(TaskSpec(action=..., payload={...}, ...)) + pop_claim_no_wait()/ack()/fail()

# 前端改动检查
node --check dashboard/static/js/*.js

# Python 侧（解禁后）
python -m pytest tests/ -q
python -m ruff check . && python -m mypy qtask_list cli dashboard remote_storage
```

工作流约定见 `AGENTS.md`；当前明确约束：**除 dashboard 目录（前端+管理后台）外不要改**；先 commit 再改、小步提交；`AGENTS.md` 用户自有改动永远不提交。
