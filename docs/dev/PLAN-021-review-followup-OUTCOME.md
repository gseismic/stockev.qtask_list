# PLAN-021 执行结果：PLAN-020 复查问题修复

- 日期：2026-09-29
- 计划：`docs/dev/PLAN-021-review-followup.md`
- 设计：`docs/design/qtask-review-remediation-20260929-overview.md`

## 实施结果

1. Worker 停机后继续维持心跳，直到阻塞领取返回、线程池任务 drain 完成。handler 准入与 `stop()` 信号通过短临界区确定先后，启动前的停止请求不会被清除。未准入 handler 的 claim 由 Lua 核验原消息和 attempt 后退回 ready，恢复 attempt、deadline 索引与并发租约；已准入任务继续完成。
2. RemoteStorage 到期待入队对象先原子从 pending 转入 GC 日志，再删除文件。手动删除也先撤销 pending；文件删除或日志确认失败时留下可重试日志，避免进程中断后入队悬空引用。
3. 跨队列搜索游标保存压缩的队列名快照，新队列出现后旧游标仍可继续；显式队列筛选仍验证一致性，畸形游标返回 400。队列详情的 `retry_wait`、`deadline_missed`、`expired` 改用有扫描预算的分页接口，前端可继续搜索；SDK `list_tasks()` 筛选扫描达到 5000 条时提示使用分页接口。
4. 删除队列后失效旧队列发现缓存；更新 Dashboard 指标回填和外存鉴权测试基线；README 与项目使用指南同步停机、分页和外存回收语义。

## 复查与验证

- 已复查状态脚本的键顺序与所有权条件、停机准入和续租时序、外存 pending→GC 的中断窗口、游标输入校验、前端分页状态，并据此补充手动删除、handler 准入及启动前停止的边界修复。
- 临时独立 Redis 上运行 `python -m pytest tests/ -q --disable-warnings`：**129 passed**。测试连接被重定向到独立实例，未清理本机既有 `6379/0` 数据。
- `python -m ruff check qtask_list tests/test_worker.py tests/test_dashboard_api.py tests/test_remote_storage.py`、`python -m mypy qtask_list`、`pnpm build`、`git diff --check` 均通过。前端构建产物已更新。

## 使用边界

- 搜索游标固定队列集合与创建时间上界；任务历史在翻页期间被删除时，偏移式分页仍可能跳项，调用方需要在保留期内完成查询。
- 手动删除外存对象需要回收 Redis 可用；删除活跃任务正在使用的外存对象仍由调用方负责确认业务影响。
