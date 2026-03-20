# qtask_list - 分布式任务队列

基于 Redis List 的分布式任务队列，支持 retry、DLQ、delay、crash recovery。

## 安装

```bash
pip install -e .
```

## 快速开始

### 1. 架构说明

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  分布式爬虫      │     │  分布式爬虫      │     │  分布式爬虫      │
│  (push 任务)    │     │  (push 任务)    │     │  (push 任务)    │
└────────┬────────┘     └────────┬────────┘     └────────┬────────┘
         │                       │                       │
         └───────────────────────┼───────────────────────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │   Redis List Queue     │
                    │   ns:fetch            │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │   fetch Worker 集群    │
                    │   (消费任务 + push 结果)│
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │   Redis List Queue     │
                    │   ns:store            │
                    └───────────┬────────────┘
                                │
                                ▼
                    ┌────────────────────────┐
                    │   store Worker        │
                    │   (中心存储)          │
                    └────────────────────────┘
```

### 2. Worker 启动顺序 (重要!)

**必须先启动 store worker，再启动 fetch worker！**

```
┌─────────────────────────────────────────────────────────┐
│  ⚠️  正确顺序                                          │
│                                                         │
│  终端 1: python examples/03_store_worker.py  (先启动!)  │
│  终端 2: python examples/02_fetch_worker.py             │
│  终端 3: python examples/01_generator.py                │
└─────────────────────────────────────────────────────────┘
```

原因：
- fetch worker 会把结果 push 到 `ns:store` 队列
- 如果没有 store worker 消费，任务会在 `store` 队列堆积
- 不会进入 DLQ，只有 worker 处理异常才会进 DLQ

### 3. 队列状态说明

| 状态 | 说明 |
|------|------|
| **Ready** | 主队列，待消费的任务 |
| **Processing** | 正在处理的任务（worker 意外退出时会丢回 Ready） |
| **Retry** | 重试队列（处理失败，进入重试） |
| **DLQ** | 死信队列（重试超过 max_retry） |
| **Delay** | 延迟队列（定时任务） |

### 4. CLI 命令

```bash
# 查看所有队列状态
python -m cli status

# 查看指定队列
python -m cli status stockev_list:fetch

# 实时监控队列
python -m cli watch stockev_list:fetch -i 2

# 清空队列
python -m cli clear stockev_list:fetch --force

# DLQ 重新入队
python -m cli requeue stockev_list:fetch --force

# 将 retry 队列移回主队列
python -m cli retry stockev_list:fetch

# Crash recovery
python -m cli recover stockev_list:fetch

# 查看历史
python -m cli history stockev_list:fetch
python -m cli history stockev_list:fetch -l 50

# 启动 worker
python -m cli worker -q fetch -n stockev_list -w 4
```

## Python SDK

### 推送任务

```python
from qtask_list import SmartQueue

q = SmartQueue(
    redis_url="redis://localhost:6379/0",
    queue_name="fetch",
    namespace="stockev_list"
)

# 单条任务
q.push({
    "action": "fetch_stock",
    "symbol": "AAPL",
    "url": "https://api.example.com/AAPL"
})

# 批量任务
q.push_batch([
    {"action": "fetch_stock", "symbol": "AAPL"},
    {"action": "fetch_stock", "symbol": "TSLA"},
])

# 延迟任务 (60秒后执行)
q.push({"action": "fetch_stock", "symbol": "AAPL"}, delay_seconds=60)
```

### Worker

```python
from qtask_list import Worker, SmartQueue

# 创建 result queue (可选)
store_q = SmartQueue(redis_url, "store", namespace="stockev_list")

# 创建 worker
worker = Worker(
    redis_url="redis://localhost:6379/0",
    queue_name="fetch",
    namespace="stockev_list",
    result_queue=store_q,  # 处理结果推送到 store 队列
    max_workers=4,           # 并发数
)

# 注册 handler
@worker.on("fetch_stock")
def handle_fetch(task):
    symbol = task["symbol"]
    price = fetch_price(symbol)  # 你的爬取逻辑
    
    # 返回结果，会自动推送到 result_queue
    return {
        "action": "store_price",
        "symbol": symbol,
        "price": price
    }

# 启动 worker
worker.run()
```

## 配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `redis_url` | `redis://localhost:6379/0` | Redis 连接 |
| `namespace` | `""` | 命名空间 |
| `max_retry` | `3` | 最大重试次数 |
| `large_threshold` | `50KB` | 大文件阈值 |

## 环境变量

```bash
export REDIS_URL=redis://localhost:6379/0
```

## 创建skill
opencode + codex/skill-creator with:
```
/skill-creator 为我创建使用本库的user guide，应详细包含examples，所有类的介绍，核心源代码的源文件，skill创建在本地 .agents中，名称为pai-qtask-list-codexskill-oc
```
