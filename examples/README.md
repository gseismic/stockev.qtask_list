# Examples

本目录包含 `qtask_list` V2 的使用示例。示例默认连接 `redis://localhost:6379/0`，
需要先启动 Redis；所有生产任务都用 `TaskSpec` 表达身份、时间窗口和血缘。

## 快速开始

### 股票数据 Pipeline

完整的 3 阶段 pipeline，涉及 2 个 namespace：

```
stockev_list:fetch → finance:calculate → stockev_list:store
```

**架构图：**

```
┌─────────────────────────────────────────────────────────────┐
│                    股票数据 Pipeline                          │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   [Generator]                                              │
│   python examples/generator.py                                │
│         │                                                   │
│         ▼                                                   │
│   ┌─────────────────┐                                        │
│   │ stockev_list:  │                                        │
│   │ fetch (10个)   │                                        │
│   └────────┬────────┘                                        │
│            │                                                 │
│            ▼                                                 │
│   ┌─────────────────┐         ┌─────────────────┐          │
│   │ stockev/       │────────▶│ finance/        │          │
│   │ fetch_worker   │         │ calculate_worker │          │
│   └─────────────────┘         └────────┬────────┘          │
│                                      │                     │
│                                      ▼                     │
│   ┌─────────────────┐         ┌─────────────────┐          │
│   │ stockev/       │◀────────│ finance/        │          │
│   │ store_worker   │         │ calculate_worker │          │
│   └─────────────────┘         └─────────────────┘          │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**运行步骤：**

```bash
# 终端1: store worker (stockev namespace)
python examples/stockev/store_worker.py

# 终端2: calculate worker (finance namespace)
python examples/finance/calculate_worker.py

# 终端3: fetch worker (stockev namespace)
python examples/stockev/fetch_worker.py

# 终端4: 生成任务（V2 enqueue_many）
python examples/generator.py
```

### 确定性 5 分钟调度

`stockev/scheduler.py` 将 UTC 时间向下规整到 5 分钟 slot，并为早盘/午盘任务设置
`logical_key`、历史分区和 `start_deadline_at`。cron 每 5 分钟调用即可安全重跑：

```bash
python examples/stockev/scheduler.py --session am
python examples/stockev/scheduler.py --session pm
```

### 新闻发现 fan-out

`stockev/news_discover.py` 展示双参数 handler 如何读取 `TaskContext`，再通过
`TaskResult.emissions` 将 URL 拆成带 `news:<sha256>` 身份的 `fetch_news` 任务。

### Reconciler 补齐

`stockev/reconciler.py` 从 JSONL 期望清单调用 `QueueAdmin.enqueue_many()`。重复运行只会
得到 `duplicate_active`/`duplicate_retained`，不会直接修改 Redis key：

```bash
python examples/stockev/reconciler.py
python examples/stockev/reconciler.py --manifest examples/stockev/tasks.jsonl
```

## 目录结构

```
examples/
├── README.md                    # 本文件
├── generator.py                 # 生成任务
│
├── stockev/                    # stockev namespace workers
│   ├── fetch_worker.py         # 爬取数据，TaskResult fan-out
│   ├── store_worker.py         # 存储结果
│   ├── scheduler.py            # 5 分钟 slot + 早晚 universe
│   ├── news_discover.py        # 新闻发现与 fan-out
│   └── reconciler.py           # 期望清单幂等补齐
│
└── finance/                    # finance namespace workers
    └── calculate_worker.py    # 计算 MA
```

## 任务流转

| 阶段 | 队列 | Namespace | Action | Worker |
|------|------|-----------|--------|--------|
| 1 | fetch | stockev_list | fetch_stock | stockev/fetch_worker.py |
| 2 | calculate | finance | calculate_ma | finance/calculate_worker.py |
| 3 | store | stockev_list | store_result | stockev/store_worker.py |

## CLI 常用命令

```bash
# 查看队列状态
python -m cli status

# 实时监控
python -m cli watch stockev_list:fetch

# 查看历史
python -m cli history stockev_list:fetch

# 清理过期历史
python -m cli clean-history stockev_list:fetch
```

## 自定义

修改 `examples/generator.py` 中的 `symbols` 列表来更改股票代码：

```python
symbols = [
    "AAPL", "TSLA", "NVDA", "MSFT", "GOOG",
    "AMZN", "META", "NFLX", "AMD", "INTC",
]
```
