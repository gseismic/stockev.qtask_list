# 交接文档（HANDOFF）

- 更新时间：2026-09-20 14:10
- 基线 commit：`ca4dde0`（PLAN-014 全部修复实施完成并推送）
- 交接人：ZCode agent（模型 builtin:bigmodel-coding-plan/GLM-5.3）
- 阅读顺序建议：本节 → 第 2 节现状 → 第 3 节未完成清单 → 第 4 节决策记录

## 1. 项目背景（零上下文可读）

**qtask_list** 是一个基于 Redis List 的分布式任务队列库（`pip install -e .`，CLI 命令 `qtask`，FastAPI + React Dashboard）。核心机制：`BRPOPLPUSH` 可靠消费 + 多子队列状态管理（ready/processing/retry/dlq/delay）+ per-worker heartbeat 的 stale-safe recovery。

**项目的最终用途**：作者要构建股票数据采集系统（工作目录名 stockev.qtask_list 即此意），三个核心场景：

1. **批量下载**股票历史数据（大批量一次性任务）；
2. **定时抓取**行情，可能一只股票一只股票地抓（大量小任务、7×24 长跑）；
3. **动态增长的抓取列表**（新闻等，运行期不断发现新条目追加抓取）。

库的定位（已明确裁决）：**只做可靠执行队列**；调度、幂等落库、数据持久化是使用方的职责。库提供去重（`logical_key`）、退避、执行截止（`expire_seconds`）等原语配合。

**环境与验证基线**：

- Redis：`redis://localhost:6379/0`（测试直接用真 Redis，不走 mock）
- 测试：`python -m pytest tests/ -q` → 当前 **114 passed**
- 静态检查：`python -m ruff check .`、`python -m mypy qtask_list cli dashboard remote_storage` → 均通过
- 手动端到端脚本：`tests/manual/e2e_plan014.py`（三场景闭环）、`tests/manual/verify_review_claims.py`（评审论断复核），直接 `python` 运行，用 Redis db15，用后自清

**关键文档索引**：

| 文档 | 内容 |
|---|---|
| `README.md` | 使用文档，含任务身份/使用约束/定时抓取部署模式（2026-09-20 重写过多章） |
| `docs/design/qtask-design-review-20260920-scenarios.md` | 我的场景化设计评审（GLM-5.3） |
| `docs/design/review-20260920-design-flaws.md` | 第二份评审（GLM-5.3-Flash） |
| `docs/dev/REVIEW-20260920-stock-data-ingestion.md` | 第三份评审（GPT-5，含实测复现） |
| `docs/design/task-identity-20260920-logical-key.md` | **任务身份与 deadline 设计定稿**（实施依据） |
| `docs/dev/PLAN-014-review-fixes.md` + `-OUTCOME.md` | 最近一轮实施的计划与结果（含 5 条行为变更说明） |
| `docs/dev/INDEX.md` | 计划-结果历史索引 |
| `skills/qtask-list-usage/SKILL.md` | 库的使用 skill（**尚未同步新特性，见未完成清单**） |

## 2. 当前状态（已完成工作）

2026-09-20 完成了"三模型交叉评审 → 合并裁决 → 全量实施"闭环：

1. 三份独立评审对核心缺口交叉印证（无周期调度、重试无退避、无去重），另各有独有发现；
2. 通过讨论确定任务身份模型（`logical_key = 类型:主体:时间桶`，时间桶编进键、TTL 只做清理）和 deadline 语义（与身份正交，过期跳过标 `skipped` 不进 DLQ）；
3. PLAN-014 实施 24 项改动 + 实施中自查修复 3 个新问题（最重要的：maintenance 线程初始化竞态会杀死 heartbeat），全部验证通过。

**库当前具备的关键能力**（细节见 README 与 PLAN-014-OUTCOME）：

- `push/push_batch` 支持 `logical_key(s)`/`dedup_ttl`/`force` 业务身份去重，`delay_seconds`/`expire_seconds`；
- 失败指数退避（`retry_backoff_base=30` 起步，`=0` 保留旧立即重试）；`max_retry=N` = 总执行次数；
- `expires_at` 写消息信封，pop 强制检查，过期跳过 + 历史 `skipped`；
- 维护线程周期接手失联 Worker 任务（`stale_recover_interval=300s`）；
- DLQ 重放默认重置重试计数（CLI `--keep-retry` 保留）；拒绝重放活跃 Worker 的 processing；
- 归档仅 terminal 状态任务；`archive_dir`/`monitor_threshold_mb`/`record_history` 可配置；
- zstd 上下文线程安全（线程局部）；外存瞬时故障延后重试、配置缺失显式报错。

**注意**：工作树中 `AGENTS.md` 有用户自己的未提交改动，**不要动它也不要提交它**（历次 commit 都刻意排除）。

## 3. 尚未完成的事项

### 3.1 优先处理（业务真正跑起来之前）

1. **examples 未更新**：`examples/stockev/` 仍是旧模式（无 logical_key、无调度示例）。README 中的"定时抓取部署模式"只有片段代码，无可运行示例。建议：新增 `examples/stockev/scheduler.py`（外置 cron 投递器，含时间桶键生成）并按新模式重写 fetch/store worker 示例。这是把库变成作者可用系统的最后一步。
2. **DLQ 无主动告警**：定时场景下单只股票失败进 DLQ 后靠下一轮自愈，但持续失败需要人看见。当前只有 Dashboard/CLI 巡检。最小方案：维护线程或独立 cron 检查 DLQ 计数超阈值时回调/通知（作者自选用什么通道）。
3. **skill 文档未同步**：`skills/qtask-list-usage/SKILL.md` 完全未提及 logical_key/退避/skipped/`--keep-retry`/新参数。另外作者机器上有两份**仓库外**安装的 skill 副本（`~/.agents/skills/qtask-usage`、`~/.agents/skills/pai-qtask-list-codexskill-oc`）也基于旧 API，需要作者决定是否同步。
4. **版本号未升级**：仍是 `0.1.1`（pyproject.toml）。本轮有 5 条行为变更（见 PLAN-014-OUTCOME），语义上应升 `0.2.0` 后再对外使用。

### 3.2 中期方向（有裁决记录，实施前先读第 4 节）

5. **keyed concurrency**（同一 symbol 不并发、跨 symbol 并行）：未实现。当前缓解 = `logical_key` 在途去重 + 文档警告（README 使用约束 #4）。若作者抓取有顺序依赖（增量分页）则需要真实现（per-key 锁或一致性哈希分片）。
6. **任务级超时 watchdog**：只有文档硬约束（handler 必须自带 HTTP timeout）。库层可做 future 超时标记 fail（僵尸线程仍占并发额度，需文档说明）。
7. **provider/symbol 级限流**：只有文档建议。若数据源有硬性 QPS 限制需在业务层 token bucket。
8. **库内调度器评估**：裁决是外置 cron 先行。若未来要进库（交易日历、多实例锁、宕机补偿、schedule 独立于任务执行），按 `task-identity` 设计文档"明确不做"节的原则重新立项设计。
9. **队列优先级**：回补与定时任务的时效冲突目前靠分队列部署缓解，库内无优先级概念。

### 3.3 长期 / 低优先

10. fan-out 专用接口（父子任务、trace_id、emit_many）——现用 handler 内 `push_batch + logical_keys` 模式替代。
11. 历史业务元数据（R-007：payload_ref/result_ref、symbol/dataset 审计索引）——目前历史只加了 `logical_key` 字段。
12. 信封元数据（`_retry`/`_large`/`_compressed`/`expires_at`）与业务 payload 彻底分离的信封化重构。
13. 队列发现 SCAN 注册表（P3 性能项，`queue_names()` 仍全库 SCAN）。
14. RemoteStorage TTL（默认 7 天）与任务生命周期（历史 15 天）对齐。
15. push 的完全原子 outbox/reconciliation（已合并单 pipeline 大幅缓解，跨 Redis 故障仍非事务）。

### 3.4 测试与工程债

16. Worker 并发路径（`_poll_once` 信号量先取后弹的重构）没有专门的线程级测试；maintenance stale recovery 只有参数传播断言（端到端验证靠 `tests/manual/e2e_plan014.py`，未纳入 pytest）。
17. Dashboard 的 `skipped` 徽章没有专属 CSS 色调（状态页签与标签已有，可用）。
18. `qtask_list/__init__.py` 的 `start_dashboard()` 未透传 Dashboard 新配置（secure_cookie 之外的参数固定默认），次要。

## 4. 重要决策记录（避免重新讨论）

| 决策 | 结论 | 记录位置 |
|---|---|---|
| 调度进库还是外置 | 外置 cron + logical_key 去重，库不做 cron/交易日历 | task-identity 设计文档"明确不做"；PLAN-014"明确不做" |
| 去重键语义 | 时间桶编进键字符串；TTL 只做垃圾清理不做窗口；失败自动释放；DLQ 死任务只占自己的桶 | task-identity 设计文档 §2 |
| deadline | 与身份正交的 `expire_seconds`；过期跳过标 `skipped` 不进 DLQ；三个生命周期（dedup TTL/expires_at/ttl_days）不混淆 | task-identity 设计文档 §3 |
| max_retry 语义 | N = 总执行次数（首次 + N-1 次重试） | README 使用约束 #3 |
| 重试默认行为 | 指数退避为默认，`retry_backoff_base=0` 保留旧立即重试兼容路径 | PLAN-014-OUTCOME 行为变更 #1 |
| DLQ 重放 | 默认重置 `_retry`（全新尝试） | PLAN-014-OUTCOME 行为变更 #2 |
| 部署模式 | 回补与定时分队列 + 各自 Worker；多 Worker 共享 `QTASK_ARCHIVE_DIR` | README 定时抓取部署模式 |

## 5. 给接手者的快速上手

```bash
# 验证环境
python -m pytest tests/ -q          # 114 passed，需要 localhost:6379
python -m ruff check . && python -m mypy qtask_list cli dashboard remote_storage

# 手动端到端（三场景闭环，用 db15 自动清理）
python tests/manual/e2e_plan014.py

# 最可能的下一步：examples/stockev 按 README 新模式重写（见 3.1 第 1 条）
```

工作流约定见 `AGENTS.md`（计划-实施-review-OUTCOME-INDEX-提交-push，docs 提交，中文 commit）。`docs/HANDOFF.md` 即本文件，按约定在用户要求时更新。
