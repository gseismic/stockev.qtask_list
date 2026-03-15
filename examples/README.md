# Examples

本目录包含 `qtask_list` 的使用示例。

## 快速开始

### 1. 股票数据 Pipeline (推荐)

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
│   python examples/stockev/00_generator.py                   │
│         │                                                   │
│         ▼                                                   │
│   ┌─────────────────┐                                        │
│   │ stockev_list:  │                                        │
│   │ fetch (10个)   │                                        │
│   └────────┬────────┘                                        │
│            │                                                 │
│            ▼                                                 │
│   ┌─────────────────┐         ┌─────────────────┐          │
│   │ fetch_worker    │────────▶│ finance:        │          │
│   │ (爬取股票数据)  │         │ calculate       │          │
│   └─────────────────┘         └────────┬────────┘          │
│                                      │                     │
│                                      ▼                     │
│   ┌─────────────────┐         ┌─────────────────┐          │
│   │ calculate_worker│────────▶│ stockev_list:   │          │
│   │ (计算MA)        │         │ store           │          │
│   └─────────────────┘         └────────┬────────┘          │
│                                      │                     │
│                                      ▼                     │
│   ┌─────────────────┐                                        │
│   │ store_worker   │                                        │
│   │ (存储结果)      │                                        │
│   └─────────────────┘                                        │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**运行步骤：**

```bash
# 1. 启动 store worker (最后消费)
python examples/stockev/store_worker.py

# 2. 启动 calculate worker (中间层)
python examples/finance/calculate_worker.py

# 3. 启动 fetch worker (最先消费)
python examples/stockev/fetch_worker.py

# 4. 生成任务 (新开终端)
python examples/stockev/00_generator.py
```

**或者使用 CLI 启动 workers：**

```bash
# 同时启动多个 workers
python -m cli worker -q store -n stockev_list &
python -m cli worker -q calculate -n finance &
python -m cli worker -q fetch -n stockev_list &

# 生成任务
python examples/stockev/00_generator.py
```

### 2. CLI 常用命令

```bash
# 查看队列状态
python -m cli status

# 实时监控
python -m cli watch stockev_list:fetch

# 查看历史
python -m cli history stockev_list:fetch

# 清理过期历史
python -m cli clean-history stockev_list:fetch

# Crash recovery
python -m cli recover stockev_list:fetch
```

## 目录结构

```
examples/
├── stockev/                  # 股票数据 pipeline
│   ├── 00_generator.py      # 生成任务
│   ├── fetch_worker.py      # 爬取 worker
│   └── store_worker.py      # 存储 worker
│
└── finance/
    └── calculate_worker.py  # 计算 worker
```

## 任务流转

| 阶段 | 队列 | Namespace | Action |
|------|------|----------|--------|
| 1 | fetch | stockev_list | fetch_stock |
| 2 | calculate | finance | calculate_ma |
| 3 | store | stockev_list | store_result |

## 自定义

修改 `examples/stockev/00_generator.py` 中的 `symbols` 列表来更改股票代码：

```python
symbols = [
    "AAPL", "TSLA", "NVDA", "MSFT", "GOOG",
    "AMZN", "META", "NFLX", "AMD", "INTC",
]
```

修改 worker 文件中的 namespace 来连接不同的队列。
