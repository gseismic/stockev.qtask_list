# PLAN-016：V2 评审问题修复与交付闭环结果

- 计划文件：`docs/dev/PLAN-016-v2-review-remediation.md`
- 完成时间：2026-09-20（Asia/Shanghai）
- 目标：修复评审发现的问题并完成测试、文档、示例、提交和推送闭环
- 工作树保护：`AGENTS.md` 未修改、未暂存、未提交

## 实施结果

### 核心代码

- `QueueAdmin` 的 `retry_wait` 统计改为使用注入的 Redis 客户端；旧 V1 payload 解码
  失败时继续尝试兼容 JSON，并在没有 V2 描述时使用安全的 legacy move 降级。
- `SmartQueue.get_stats()` 同时暴露 `retry_wait`，SDK 与 Admin 的统计字段保持一致；
  前端总 live/priority 计算不会把 delay 中的 retry_wait 重复计数。
- V2 DLQ replay 保持新 task_id 与血缘；V1 记录可以原地迁移，批量路径遇到旧格式或
  不完整描述时不会丢任务。移动成功才更新 pending 状态，moved=0 不修改历史。
- Dashboard 的无 action、非法 datetime、无效 replay 等用户输入统一返回 4xx；服务端
  异常仍保留 5xx 语义。
- Worker 增加独立 maintenance wakeup 事件，`TaskContext.stop_requested` 绑定的停止
  事件不会被维护线程清除；executor 仍在 drain 时会保持 maintenance heartbeat/lease，
  优雅 drain 和 finally 竞态均会唤醒维护线程。
- 前端将 `retry_wait` 与旧 retry family 计数统一展示，但不为 retry_wait 提供不支持的
  手动 drain 操作。

### 测试与手工验证

- 按 V2 语义迁移旧队列、Worker、集成、CLI、Dashboard、归档测试，覆盖 V1/V2 双读、
  delay retry、attempt、deadline、dedup owner、terminal TTL、minimal history、新实例
  replay、Dashboard 4xx 和 drain stop。
- `tests/manual/verify_review_claims.py` 改为验证 retained live history、退避重试、
  DLQ replay 新 id/血缘和无效 payload 拒绝，并在 finally 中清理测试 key。

### 文档与示例

- README 和 `skills/qtask-list-usage/SKILL.md` 已去除 V1 `_retry`/TTL/replay 误导，补充
  V2 `TaskSpec`、`retry_wait`、`start_deadline_at`、replay 血缘和危险操作确认。
- `examples/stockev/` 新增 `scheduler.py`、`news_discover.py`、`reconciler.py`，并将
  pipeline producer/worker 更新为 TaskSpec/TaskResult 用法。
- 补齐 `PLAN-015-qtask-v2-OUTCOME.md`，并在 `docs/dev/INDEX.md` 登记 PLAN-015/016。

## 验证命令

```text
python -m pytest tests/ -q                         # 118 passed
python -m ruff check .                             # All checks passed
python -m mypy qtask_list cli dashboard remote_storage
node --check dashboard/static/js/*.js
python -m compileall -q examples
```

提交前另执行 `git diff --check`，并确认 `AGENTS.md` 不在暂存区；提交后立即推送并核对
本地 `HEAD` 与 `origin/main` 一致。
