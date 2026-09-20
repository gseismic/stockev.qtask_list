# qtask_list 设计评审报告（股票数据场景）

- 日期：2026-09-20
- 评审基线：commit `83b5eca`（修复系统 review 发现的问题）
- 评审方式：代码通读（queue.py / worker.py / admin.py / history.py / archiver.py / storage.py / examples/stockev）+ fakeredis 复现脚本实测 + python-zstandard 官方文档查证
- 评审工具：ZCode agent，模型 `builtin:bigmodel-coding-plan/GLM-5.3`
- 评审依据：api-design-zh skill（用户接口 vs 内部接口评审框架）

## 0. 评审目标场景

用户声明的三个核心使用场景：

1. **批量下载**：一次性下载股票历史数据（大批量任务推送）。
2. **定时抓取**：周期性抓取数据，可能需要一只股票一只股票地抓（大量小任务、长周期运行）。
3. **动态增长列表**：抓取对象列表在运行期动态增加（如新闻条目发现后追加抓取）。

## 1. 总体结论

核心架构是扎实的：`BRPOPLPUSH` 可靠消费、worker 专属 processing key + heartbeat 的 stale-safe recovery、QueueAdmin 管理层抽象，方向都正确。

但对照上述三个场景，存在：

- **4 个核心设计缺口**：无周期调度原语、重试无退避、无任务去重、`expire_seconds` 不在执行期生效；
- **2 个正确性 bug**：zstd 压缩上下文跨线程共享（违反库的线程安全契约）、RemoteStorage 配置不对称时静默丢任务；
- **1 个行为不一致**（已实测）：压缩/外存大 payload 的 handler 永远看到 `_retry=0`。

其中**定时抓取场景目前的支撑最弱**。

## 2. 场景逐项分析

### 2.1 场景 1：批量下载 —— 基本可用，两个隐患

**（a）重试无退避（P1，三个场景共同刚需）。**
`Worker._poll_once()`（qtask_list/worker.py:179-185）每个轮询周期调用 `move_retry()`，把 retry 队列立即搬回主队列。即任务失败后约 2 秒内重试，`max_retry=3` 在约 10 秒内烧完。数据源临时故障或限流 5 分钟时，批量推送的 5000 个任务会在几秒内完成约 2 万次对故障 API 的无效请求，然后全部涌入 DLQ——恰好在 API 最脆弱的时候加重它。爬虫场景重试必须带指数退避。

**（b）`push_batch()` 与 `push()` 逻辑重复且能力不一致（P2，可维护性）。**
`push_batch()`（qtask_list/queue.py:113）不支持 `delay_seconds` / `expire_seconds`，且完整复制了压缩与大 payload 外存逻辑（queue.py:118-135 vs queue.py:82-106），历史写入也是独立实现。两份实现必然漂移：`push` 改了历史 schema 或 wrapper 格式时，`push_batch` 不会跟着改。批量推送是本场景主路径，应让 `push_batch` 复用 `push` 的内部序列化函数。

**（c）交付语义未文档化（P3）。**
crash recovery 会重跑"已执行但未 ack"的任务，系统是 at-least-once 交付。下载落库逻辑必须幂等（按 symbol+date upsert）。README 未声明此约束，应补充。

### 2.2 场景 2：定时抓取（逐只股票）—— 支撑最弱的场景

**（a）没有周期任务原语（P1 设计缺口）。**
只有一次性 `delay_seconds`。实现"每 5 分钟抓一只股票"只能在 handler 里自我续期：

```python
@worker.on("fetch_quote")
def fetch_quote(task):
    data = fetch(task["symbol"])
    q.push({"action": "fetch_quote", "symbol": task["symbol"]}, delay_seconds=300)
```

该模式有三个失败路径，库层面均无防护：

1. **周期链静默断裂**：任务连续失败进 DLQ 后无人推下一轮，该股票的定时抓取悄无声息停止，只能人工翻 Dashboard 发现。
2. **周期链分裂**：handler 先 push 下一轮、后 ack。若 push 成功后、ack 前 worker 崩溃，recovery 重跑 handler 再次 push → 同一股票出现两条周期链，频率翻倍且永不收敛。该模式与 at-least-once 语义天然冲突，库层面没有去重兜底。
3. **周期漂移**：interval 从完成时刻起算，抓取慢时实际周期大于名义周期。

**（b）存活 Worker 不接手崩溃 Worker 的任务（P1 可用性缺口）。**
`recover_stale_processing()` 只在 `run()` 启动时执行一次（qtask_list/worker.py:236）；`_maintenance_loop`（qtask_list/worker.py:132-177）只做 heartbeat 刷新、内存检查和归档，不做 stale recovery。跑 2 个 worker 时其中一个崩溃，其 processing 任务一直卡住，直到另一个 worker 重启或人工执行 `qtask recover`。定时抓取是长跑场景，maintenance 线程应周期性执行 stale recovery（heartbeat TTL 判断条件已具备，改动小）。

**（c）`expire_seconds` 只是视图标记，不阻止执行（P1 数据质量）。**
过期判定只存在于 admin 查询视图（qtask_list/admin.py:781-813 `_read_expired`），`pop` 与 Worker 完全不检查 `expires_at`。队列积压时（例如批量下载占满 worker），过期数小时的行情任务照样被抓取并存库——把陈旧数据当最新数据写入。对定时行情，过期任务应在 pop 时跳过并直接丢弃/DLQ。

**（d）无优先级，定时任务会被批量任务饿死（P2）。**
单队列 FIFO，且 `move_delay` / `move_retry` 都 LPUSH 到队尾。批量回补 5000 个任务在前时，5 分钟一次的行情任务可能排队数小时。"回补"与"定时"是时效性完全不同的两类任务，当前只能靠物理分队列 + 各起 worker 解决；但 Worker 绑定单队列、并发数静态分配，没有跨队列共享算力的方式。该约束至少应作为部署模式写入文档。

### 2.3 场景 3：动态增长列表（新闻等）—— 缺一个关键原语

**（a）没有任务去重（P1）。**
`push` 每次生成新 `uuid4`（qtask_list/queue.py:82），`task_id` 无业务含义。动态列表的典型模式是"发现任务定期扫列表 → 为每个新条目推抓取任务"，但恰在 API 慢导致积压时（上一轮抓取未消费完），下一轮发现任务又推一遍 → 同一条目重复抓取、重复存储。需要的语义是：

```python
q.push({"action": "fetch_news", "news_id": nid}, dedup_key=f"news:{nid}")
```

实现为 Redis `SET NX` + TTL，已存在则跳过。该原语同时根治 2.2(a) 的周期链分裂问题。是三个场景中性价比最高的新增 API。

**（b）无 per-key 串行化（P2，文档义务）。**
`max_workers>1` 时同一 symbol 的两个任务可能并发执行。行情覆盖写无影响，但新闻增量分页抓取等有顺序依赖的任务会乱序。实现代价较高（consistent hashing 或 per-key 锁），至少应在文档中明确警告。

**（c）高频场景下历史记录是强制开销（P2）。**
每个 push 必写 5 个 Redis 操作（lpush + hset + expire + zadd + expire，qtask_list/history.py:32-57），历史 hash 保留 15 天，归档只清 1 天前的（qtask_list/archiver.py:96，`days_ago=1`）。定时抓取 5000 只股票 × 每 5 分钟一次 ≈ 每天 140 万任务，Redis 常态驻留约一整天的历史（约 140 万个 hash）。历史对高频定时任务价值密度低，应支持按队列关闭或降采样。

## 3. 正确性 bug（实测/查证）

### P1：zstd 压缩上下文跨线程共享，违反库的线程安全契约

- 位置：qtask_list/queue.py:64-65（`self._cctx` / `self._dctx` 为实例级共享）。
- 路径：`max_workers>1` 时 `_process_task` 在线程池中并发执行（qtask_list/worker.py:96-130），其中 `self.result_queue.push(result)`（worker.py:123-124）对超过 `compress_threshold` 的 payload 调用 `self._cctx.compress()`（queue.py:91）。多个池线程共享同一个 `result_queue` SmartQueue 实例 → 并发调用同一 `ZstdCompressor`。
- 查证：python-zstandard 官方文档与 issue 跟踪明确 **`ZstdCompressor` 实例不能被多个 Python 线程同时调用**，建议每线程一个实例。参见 [python-zstandard 文档](https://python-zstandard.readthedocs.io)、[GitHub Issue #7403](https://github.com/indygreg/python-zstandard)。
- 场景命中：K 线历史 JSON 轻松超过 50KB 阈值；4 个并发 fetch 线程同时向 result_queue 推大结果 → 数据竞争，可能崩溃或产出损坏数据。
- 修复：`threading.local()` 持有压缩/解压上下文（每线程实例）。

### P2：RemoteStorage 配置不对称时静默丢任务

- 位置：qtask_list/queue.py:162-170（`_load_large_payload`）、qtask_list/worker.py:106-110。
- 路径：生产端配置了 `storage`、消费端 Worker 未配置（examples/stockev 均未配置）时，大 payload 到达消费端后 `_load_large_payload` 原样返回 wrapper `{"_large": True, "key": ...}`，handler 拿不到 `action` → 报 "no action" 失败 → 重试耗尽进 DLQ。错误信息完全误导，真实原因是配置缺失。
- 修复：消费端检测 `_large` 且无 `storage` 时显式报错（写入日志与 history reason），或在文档中强约束并在启动时校验。

### P2：压缩/外存大 payload 的 handler 永远看到 `_retry=0`（已实测）

- 位置：qtask_list/queue.py:162-170（解压/外存加载后返回的完整 payload 不含 `_retry`）、qtask_list/queue.py:319-321（`fail()` 把 `_retry` 写在外层 wrapper 上）。
- 实测：复现脚本 `/tmp/qtask_review/repro_infinite_retry.py`（fakeredis，`compress_threshold=10` 强制压缩，`max_retry=3`）。结果：连续 fail 3 次后正常进入 DLQ，`max_retry` 生效；**但 handler 每一轮收到的 `_retry` 都是 0**。
- 评审过程说明：最初假设该问题会导致无限重试，实测证伪（重试计数保存在消息 wrapper 中随队列流转），本条降级为行为不一致问题。
- 影响：小 payload 的 handler 能看到递增的 `_retry`、大 payload 的永远看不到。handler 无法基于重试次数做退避或放弃决策；且 `_retry` 字段对小 payload 会泄漏进 handler 收到的业务 payload（概念泄漏，轻微）。
- 测试缺口：tests/test_queue.py:50-66 只覆盖小 payload 重试路径，压缩/外存路径无重试测试。

### P3：`push_batch` 中途异常泄漏外存对象

- 位置：qtask_list/queue.py:118-152。循环内 `storage.save_bytes()` 立即执行，`pipe.execute()` 最后才执行；中间某个 payload 序列化失败时，前面已上传的外存对象成为孤儿（仅靠服务端 7 天 TTL 兜底）。

## 4. 修改建议（按优先级）

| 优先级 | 改动 | 理由 | 预估量级 |
|---|---|---|---|
| 1 | 重试退避：`fail()` 改为推入 delay 队列，延迟 `base * 2^retry` | 场景 1/2 共同刚需；delay 机制已现成 | 小 |
| 2 | `push(..., dedup_key=)`：SETNX + TTL 去重 | 场景 3 刚需；顺带根治周期链分裂 | 小 |
| 3 | maintenance 线程周期性执行 `recover_stale_processing` | 长跑可用性缺口 | 小 |
| 4 | zstd 上下文线程局部化；storage 配置不对称显式报错 | 两个正确性 bug | 小 |
| 5 | `pop` 时强制检查 `expires_at`：过期直接丢弃/DLQ | 定时行情数据质量 | 小-中 |
| 6 | 周期调度原语：短期先文档化 self-reschedule 模式风险 + DLQ 告警；长期做 Scheduler（ZSET 存 schedule，maintenance 驱动） | 场景 2 根本解 | 中 |
| 7 | `push_batch` 复用 `push` 内部实现；历史写入支持按队列 opt-out / 降采样 | 可维护性 + 高频成本 | 中 |
| 8 | 文档：at-least-once 幂等要求、回补与定时分队列部署模式、per-key 并发警告 | 低成本高价值 | 小 |

其中 1-4 均为小改动，却覆盖三个场景中最疼的问题；5-8 需要一定设计，建议先在 docs/design 立设计文档再动手实施。

## 5. 附录

### 5.1 复现脚本

`/tmp/qtask_review/repro_infinite_retry.py`（fakeredis + lupa 执行 Lua）。核心输出：

```
round 0: handler 收到 _retry=0
round 1: handler 收到 _retry=0
round 2: handler 收到 _retry=0
max_retry=3，实际 fail 3 次后进入 DLQ（计数生效，但 handler 不可见）
```

### 5.2 关键代码位置索引

| 主题 | 位置 |
|---|---|
| 重试立即执行 | qtask_list/worker.py:179-185（`_poll_once` 每周期 `move_retry`） |
| 无周期原语 | qtask_list/queue.py:71-75（`push` 仅支持一次性 delay） |
| stale recovery 仅启动时 | qtask_list/worker.py:236；maintenance 无恢复：qtask_list/worker.py:132-177 |
| expire 仅视图 | qtask_list/admin.py:781-813；pop 无检查：qtask_list/queue.py:227-253 |
| 无去重 | qtask_list/queue.py:82（每次新 uuid4） |
| zstd 共享上下文 | qtask_list/queue.py:64-65；并发调用路径 worker.py:123-124 → queue.py:91 |
| storage 不对称 | qtask_list/queue.py:162-170 |
| push/push_batch 重复 | qtask_list/queue.py:71-111 vs 113-153 |
| 历史强制写入 | qtask_list/history.py:32-57；归档仅清 1 天前：qtask_list/archiver.py:96 |

### 5.3 参考来源

- [python-zstandard 官方文档（线程安全约束）](https://python-zstandard.readthedocs.io)
- [python-zstandard GitHub Issue #7403](https://github.com/indygreg/python-zstandard)
- api-design-zh skill：用户接口 vs 内部接口评审框架
