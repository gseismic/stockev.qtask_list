# PLAN-020 执行结果：全代码评审问题修复

- 日期：2026-09-29
- 计划：`docs/dev/PLAN-020-review-remediation.md`
- 设计：`docs/design/qtask-remediation-20260929-overview.md`

## 逐项结果

1. Worker 心跳与租约续期改由独立线程负责；慢归档、诊断和告警不再占用续租循环。`begin_attempt` 成功后立即登记临时 claim，覆盖外存下载及线程池等待阶段；停止时持续续租直到 handler 退出。
2. RemoteStorage 默认监听本机，远程访问需要 Bearer token；上传限制为默认 64 MiB。Dashboard 健康接口和启动日志仅展示去凭据的 Redis 主机与端口。
3. 新外存对象使用任务独占随机键。上传写入 24 小时待入队回收记录，入队或重放成功时原子移除，进程中断产生的孤儿对象可自动清理。任务在 live 和 DLQ 时保留对象；完成、跳过、取消、DLQ 清理或重放后，Redis 原子转换写入按存储实例区分的回收日志，存储服务定期消费。旧内容寻址对象仍可读且不按单任务删除。
4. 管理删除通过原子取消脚本处理运行消息，并拒绝活跃 processing。清空默认保留 processing；显式清历史仅删除无运行消息的终态记录；删除队列要求先停止 Worker。Dashboard 确认文案与默认范围一致。
5. 总览从状态转换累计数、deadline 索引、retry_wait 计数和 Worker 注册表读取，不再每轮按队列采样 2000 条历史或遍历 delay 消息。旧队列可在维护窗口运行 `qtask rebuild-observation <queue>` 回填，未回填时界面有标记；旧键发现最多每小时一次。
6. 跨队列任务搜索按历史创建时间合并，先过滤后分页，包含 Redis 中保留的终态记录，并返回游标、是否还有数据与扫描上限标记。前端支持继续搜索。
7. 前端剩余量只计 ready、processing、旧 retry 和 delay；速率以累计完成数差值计算，计数重置时清空窗口。
8. `start_dashboard()` 使用当前包路径启动。`enqueue_many()` 异常时通过 `BatchEnqueueError` 暴露已经完成的输入前缀。

## 复查与验证

- 已复查状态脚本键顺序、租约登记窗口、管理删除竞态、外存对象所有权、游标续页和旧队列回填边界。
- `python -m compileall -q qtask_list`、`python -m ruff check qtask_list`、`python -m mypy qtask_list`、`pnpm build` 和 `git diff --check` 均通过。
- 本次未执行自动化或运行时测试；运行时行为仍需在目标 Redis 和存储服务部署环境中验证。

## 使用边界

- 回收消费者应连接任务使用的同一 Redis，且需保持服务运行；存储服务离线时回收日志保留待重试。
- 部署前已有的共享内容对象沿用旧 TTL，不能安全地按单任务回收；已经过期的旧对象无法恢复。新任务和重放后新建的对象适用独占回收机制。
- 搜索范围为 Redis 历史索引与其中仍可读的记录；SQLite 归档和外存正文不参与全文搜索。历史过期期间游标位置可能变化，调用方可按 task_id 去重。
- 旧队列回填应暂停投递与消费；回填前总览会标记指标尚未补齐。
