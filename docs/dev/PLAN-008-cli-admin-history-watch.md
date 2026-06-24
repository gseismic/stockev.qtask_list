# PLAN-008 CLI 历史与监控入口收敛到 QueueAdmin

## 目标

继续完成 CLI 管理入口与 `QueueAdmin` 的统一：将实时监控、历史列表和历史清理入口收敛到 `QueueAdmin`，减少 CLI 中残留的 Redis 直接读取和 `SmartQueue.history` 直接调用。

## 当前证据

1. `QueueAdmin.queue_stats()` 已能提供 watch 所需统计字段。
2. `QueueAdmin.list_tasks(..., state=history)` 已能读取队列历史。
3. CLI 仍保留直接实现：
   - `watch` 使用本地 `get_queue_stats()`。
   - `history <queue>` 直接调用 `SmartQueue.history.list()`。
   - `clean-history` 直接创建 `SmartQueue` 并调用 `history.clean_expired()`。

## 实施方案

1. 为 `QueueAdmin` 增加 `clean_history()`：
   - 支持指定队列清理。
   - 支持全队列清理。
   - 支持 CLI 传入 `ttl_days`。
2. CLI `watch` 改为调用 `QueueAdmin.queue_stats()`。
3. CLI `history <queue>` 改为调用 `QueueAdmin.list_tasks(..., state=history)`。
4. CLI `clean-history` 改为调用 `QueueAdmin.clean_history()`。
5. 删除 CLI 中不再使用的本地统计 helper。
6. 补充测试：
   - `history <queue>` 使用统一历史读取仍能显示任务。
   - `clean-history` 指定队列会清理过期历史。
   - `clean-history` 全队列会清理多个队列。

## 验证

- `pytest tests/test_cli.py -q`
- `pytest -q`
- `ruff check .`
- `mypy qtask_list cli dashboard remote_storage`
