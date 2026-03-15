# Examples

本目录包含 `qtask_list` 的使用示例。

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

# 终端4: 生成任务
python examples/generator.py
```

## 目录结构

```
examples/
├── README.md                    # 本文件
├── generator.py                 # 生成任务
│
├── stockev/                    # stockev namespace workers
│   ├── fetch_worker.py         # 爬取数据
│   └── store_worker.py         # 存储结果
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
