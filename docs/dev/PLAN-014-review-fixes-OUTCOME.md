# PLAN-014 实施结果

- 日期：2026-09-20 14:00
- 计划文件：`PLAN-014-review-fixes.md`
- 状态：全部完成
- 验证：pytest 114 passed（含 22 个新增用例）、ruff 通过、mypy 通过、端到端场景验证通过

## 实施摘要

按计划完成全部 24 项改动，另修复实施过程中发现的 3 个新问题（见"计划外修复"）。

### 核心变更

**qtask_list/queue.py**
- `logical_key` 业务身份去重：`push`/`push_batch` 支持 `logical_key`/`logical_keys`/`dedup_ttl`/`force`，SETNX+TTL 占位，命中返回 `None`；投递失败自动释放已占用的身份键
- 重试退避：`fail()` 默认（`retry_backoff_base=30`）按 `base * 2^(retry-1)` ±10% 抖动写入 delay ZSET（上限 `retry_backoff_max=3600`）；`=0` 保留旧立即重试路径；信封 `expires_at` 跨重试保留
- deadline 强制：`expires_at` 写入消息信封，`pop`/`pop_no_wait` 检查，过期任务 LREM 丢弃 + 历史标 `skipped`（不进 DLQ），并继续取下一条
- zstd 压缩/解压上下文线程局部化（修复跨线程共享 `ZstdCompressor` 数据竞争）
- 外存错误分类：无 storage 的 `_large` → 显式 ValueError 进 DLQ（配置错误）；`storage.load` 网络失败 → `TransientPayloadError` → 移入 delay 延后重试
- `push` 历史记录与入队合并单 pipeline；`_wrap_payload`/`_build_envelope`/`record_pipeline` 抽出供 `push_batch` 复用（消除双实现漂移）
- `push_batch` 补 `delay_seconds`/`expire_seconds`/`logical_keys` 参数
- `move_delay` Lua 单次迁移上限 500 条
- `requeue_dlq(reset_retry=True)` 默认剥离 `_retry`（人工重放=全新尝试）

**qtask_list/worker.py**
- 维护线程周期性 `recover_stale_processing`（`stale_recover_interval=300s`），存活 Worker 自动接手失联 Worker 的任务
- 信号量改为 pop 前带超时获取（修复 stop 卡死与任务滞留）；二次停止信号强制退出
- 主循环异常退避 sleep(1)；新增 `archive_dir`/`monitor_threshold_mb` 参数，backoff/record_history 透传

**qtask_list/admin.py**
- `QueueState.skipped`；list_tasks/resolve_payload 支持 skipped；`queue_stats` 增加 skipped 计数
- `requeue_task`/`requeue_dlq` 支持 `reset_retry`（默认 True）；从活跃 Worker 的 processing 重放被拒绝
- `_dctx` 改为按次创建（FastAPI 线程池并发安全）；`_is_state_key` 过滤 `:dedup:` 键

**qtask_list/archiver.py**
- 只归档 terminal 状态（completed/failed/skipped）；live 任务历史保留 Redis；offset 翻页避免全 live 批次死循环

**CLI / Dashboard / 文档**
- `qtask requeue --keep-retry`；dashboard 状态页签增加"已跳过"
- README 新增：任务身份与去重（键模板+deadline 策略表）、使用约束（at-least-once/幂等/超时/max_retry 语义）、定时抓取部署模式（外置 cron 示例）、参数表更新

### 计划外修复（实施中发现）

1. **maintenance 线程初始化竞态致命**：两个 Worker 同时启动时 `ArchiveManager` 的 `os.makedirs` 抛 `FileExistsError`，异常在 try 之外导致维护线程死亡 → heartbeat 停刷 → 存活 Worker 被误判 stale 被抢任务。修复：`makedirs(exist_ok=True)` + 初始化包 try（archiver/monitor 可为 None，heartbeat 永不因归档故障中断）。端到端验证发现。
2. **admin `_drain_list_to_ready` 的 `reset_retry` 未实现**：批量 DLQ 重放仍原样搬运 `_retry`。修复为 lindex + Lua LREM/LPUSH 带内容重写。
3. **push 占用身份键后失败不释放**：孤儿键占住 logical_key 到 TTL。修复：异常时删除已占用键（batch 同理）。

## 验证记录

- 单元/集成测试：`python -m pytest tests/ -q` → **114 passed**（原 92 + 新增 22，其中 3 个旧用例显式改用 `retry_backoff_base=0` 兼容路径）
- 静态检查：ruff 通过、mypy（qtask_list cli dashboard remote_storage）通过
- 端到端场景验证（`/tmp/qtask_review/e2e_plan014.py`，真实 Worker 双线程 + Redis db15）：
  - 定时抓取：同桶 logical_key 第二次投递被去重 ✓
  - 失败退避：AAPL 失败 2 次后经 delay 退避重试成功，handler 可见 `_retry` ✓
  - 过期跳过：MSFT 信封过期后 pop 时跳过、历史 `skipped`、未进 DLQ、未执行 ✓
  - 动态 fan-out：discover 任务产出 2 条新闻子任务（带身份键），news worker 全部消费 ✓
  - 终态：DLQ=0、队列清空、无残留 ✓

## 行为变更说明（需知悉）

1. `fail()` 默认走退避（30s 起步）而非立即重试；旧语义用 `retry_backoff_base=0`
2. DLQ 重放默认重置 `_retry`；保留原值用 `requeue_dlq(reset_retry=False)` / CLI `--keep-retry`
3. `push` 带 `expire_seconds` 的任务超期会在消费时被跳过（此前会照常执行）
4. 消费端遇到 `_large` 消息但未配置 storage 时进 DLQ 并带明确 reason（此前静默按 "no action" 失败）
5. 归档不再删除 live 任务历史；多 Worker 部署需共享 `QTASK_ARCHIVE_DIR`
