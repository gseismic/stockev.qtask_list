# 07 Advanced：调度、动态 fan-out 与 Reconciler

三个生产级模式，各自独立运行。

## 1. 确定性调度（scheduler.py）

cron/systemd timer 每 5 分钟调用一次；时间向下取整到 slot，身份用**计划时刻**
而非进程启动时刻 —— cron 抖动、补跑、调度进程崩溃都安全：

```bash
python examples/07_advanced/scheduler.py --session am
python examples/07_advanced/scheduler.py --session pm
```

- 两种身份粒度：universe 每半天一个（`universe:{date}-{session}`），
  行情每股票每 5 分钟桶一个（`quote:{symbol}:{bucket}`）
- 重复运行得到 duplicate 是正常输出，不是错误

## 2. 动态 fan-out（news_discover.py）

「发现任务」拆成「条目任务」的标准模式，条目身份 = URL 内容哈希：

```bash
# 终端 1
python examples/07_advanced/news_discover.py

# 终端 2：投递一轮发现任务
python -m qtask_list.cli push stockev_list:news-discover \
  '{"action":"discover_news","urls":["https://example.com/a","https://example.com/b"]}'
```

- 两层身份互不干扰：发现任务建议用调度窗口身份（由 scheduler 提供）；
  条目任务身份 = `news:{sha256(url)}`
- 演示 `context.stop_requested` 协作式停止：停机时不做 fan-out，下轮重新发现仍被去重

## 3. Reconciler 幂等补齐（reconciler.py）

周期性读取 JSONL 期望清单，用 `QueueAdmin.enqueue_many()` 补齐缺失任务：

```bash
python examples/07_advanced/reconciler.py
python examples/07_advanced/reconciler.py --manifest examples/07_advanced/tasks.jsonl
```

- **只补齐、不破坏**：不直接操作 Redis key、不删除未知任务
- 重复执行只会得到 structured duplicate，可放心高频运行

## 学习点

- 确定性时间桶是幂等调度的基石（对比 05_idempotency 的单次去重）
- fan-out 的下游任务必须自带身份（内容哈希），否则重试会造成重复处理
- 调度（scheduler）、补偿（reconciler）、执行（worker）三种进程职责分离
