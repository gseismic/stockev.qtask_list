# PLAN-014：三轮评审修复实施

- 日期：2026-09-20
- 依据：三份评审报告合并结论
  - `docs/design/qtask-design-review-20260920-scenarios.md`（GLM-5.3）
  - `docs/design/review-20260920-design-flaws.md`（GLM-5.3-Flash）
  - `docs/dev/REVIEW-20260920-stock-data-ingestion.md`（GPT-5）
  - 设计定稿：`docs/design/task-identity-20260920-logical-key.md`

## 目标

落实三轮评审 + 后续讨论确定的全部修复，使 qtask_list 满足三个使用场景（批量下载、定时逐只抓取、动态增长列表）。设计定稿见 task-identity 文档。

## 改动清单

### qtask_list/queue.py
1. `logical_key` 去重：push/push_batch 支持 `logical_key`/`logical_keys`/`dedup_ttl`/`force`，SETNX+TTL，命中返回 None。
2. 重试退避：`fail()` 按 `retry_backoff_base`（默认 30s，0=旧立即重试）写入 delay ZSET，指数退避 + ±10% 抖动，上限 `retry_backoff_max`（默认 3600s）。
3. deadline 强制：`expires_at` 写入消息信封；pop/pop_no_wait 检查，过期任务跳过执行、LREM processing、历史标 `skipped`，不进 DLQ。
4. zstd 上下文线程局部化（修复跨线程共享 `ZstdCompressor` 的数据竞争）。
5. 外存错误分类：`_large` 无 storage → 显式 ValueError（进 DLQ，配置错误）；storage.load 网络失败 → `TransientPayloadError`，移入 delay 30s 后重试，不进 DLQ。
6. push 单管道化：历史记录 + 入队合并为一个 pipeline（缓解非原子窗口）。
7. push_batch 补 `delay_seconds`/`expire_seconds`/`logical_keys` 参数，序列化逻辑复用 push 的内部实现。
8. `move_delay` Lua 每次迁移加批量上限（500）。
9. `requeue_dlq(reset_retry=True)`：重放默认剥离 `_retry`（人工重放=全新尝试）。

### qtask_list/worker.py
10. maintenance 线程周期性 `recover_stale_processing`（`stale_recover_interval` 默认 300s），补"存活 Worker 不接手崩溃 Worker 任务"缺口。
11. 信号量改为 pop 前获取（带 1s 超时循环 + running 检查），修复 stop 卡死与任务滞留 processing；pop 空时释放许可。
12. 二次 Ctrl/SIGTERM 强制退出（第一次优雅，第二次 KeyboardInterrupt）。
13. worker 循环异常退避 sleep(1)（Redis 断连不再刷屏空转）。
14. 参数透传：`retry_backoff_base`/`retry_backoff_max`/`record_history`；新增 `archive_dir`（默认 env QTASK_ARCHIVE_DIR 或绝对路径）、`monitor_threshold_mb`、`stale_recover_interval`。

### qtask_list/admin.py
15. `QueueState.skipped`；list_tasks 支持 skipped 状态过滤；resolve_payload 的终态分支覆盖 skipped。
16. `requeue_task`/`requeue_dlq` 支持 `reset_retry`（默认 True）；从 active processing 重放时检测 heartbeat 并拒绝。
17. `_drain_list_to_ready` 支持 reset_retry（move_retry 不重置）。
18. `_is_state_key` 增加 `:dedup:` 标记；`_dctx` 改为每次调用新建（FastAPI 线程池并发安全）。

### qtask_list/archiver.py
19. 只归档 terminal 状态（completed/failed/skipped），live 任务历史保留在 Redis（修复"归档删 live 历史"）。

### qtask_list/history.py
20. 抽出 `record_pipeline`（push/push_batch 复用），serialize 逻辑共享。

### CLI / Dashboard
21. `qtask requeue --keep-retry` 选项；dashboard 复用 admin 默认（重放即重置），状态枚举自动获得 skipped。

### README.md
22. 新增：任务身份与去重（键模板表）、deadline 策略表、at-least-once/幂等/超时约束、外置 cron 调度模式（场景 2）、分队列部署模式、归档目录共享要求。

### tests
23. 更新受影响测试（retry 路径改为显式 `retry_backoff_base=0`）。
24. 新增：dedup、退避入 delay、过期跳过 skipped、外存瞬时失败延后/无 storage 进 DLQ、DLQ 重放重置、push_batch 新参数、归档仅 terminal、信封保留 expires_at。

## 验收标准
- 全量 pytest 通过（含新增用例），ruff、mypy 通过。
- 旧行为兼容：`retry_backoff_base=0` 时 retry list 语义不变；无 logical_key 的 push 行为不变（返回 task_id）。
- 三个场景的部署模式在 README 有可直接照抄的代码。

## 明确不做（记录裁决）
- 库内 cron/交易日历调度（外置 cron 方案）。
- 队列发现 SCAN 注册表（P3 性能项，无语义缺口）。
- fan-out 专用接口（handler 内 push_batch + logical_key 模式替代）。
- 信封元数据与业务 payload 的彻底分离（后续版本）。
