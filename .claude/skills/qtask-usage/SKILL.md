---
name: qtask-usage
description: This skill should be used when the user asks to "use qtask_list", "how to use queue", "task queue example", "smartqueue usage", "worker tutorial", "queue example", "push task", "create worker", "delay task", "result queue", "pipeline example", "cli command", "dashboard usage", "task history", or needs help with the qtask_list distributed task queue library.
---

# qtask_list Usage Guide

本 skill 提供 qtask_list 分布式任务队列库的使用指南。

## 快速开始

### 安装

```bash
pip install -e .
pip install -e ".[dev]"
pip install -e ".[dashboard]"
```

设置环境变量：
```bash
export REDIS_URL=redis://localhost:6379/0
```

### 基础用法 - SmartQueue

创建队列实例：
```python
from qtask_list import SmartQueue

q = SmartQueue("redis://localhost:6379/0", "my_queue", namespace="myapp")
```

推送任务：
```python
# 单个任务
task_id = q.push({"action": "process_data", "data": {"key": "value"}})

# 批量任务
task_ids = q.push_batch([
    {"action": "task1", "data": "a"},
    {"action": "task2", "data": "b"},
])

# 延迟任务 (60秒后执行)
task_id = q.push({"action": "delayed_task"}, delay_seconds=60)
```

消费任务：
```python
task = q.pop()
if task:
    task_id, payload = task
    # 处理任务...
    q.ack(task_id)  # 确认成功
    # 或 q.fail(task_id)  # 标记失败
```

### Worker 用法

创建 Worker：
```python
from qtask_list import Worker

worker = Worker(
    redis_url="redis://localhost:6379/0",
    queue_name="my_queue",
    namespace="myapp",
    max_workers=4,
)
```

注册任务处理器：
```python
@worker.on("process_data")
def handle_process(task):
    data = task["data"]
    # 处理逻辑...
    return {"status": "done", "result": data}
```

启动 Worker：
```python
worker.run()
```

### 任务链 (Result Queue)

实现任务流水线：
```python
from qtask_list import Worker, SmartQueue

# 下游队列 - handler 返回值自动推送到此队列
next_q = SmartQueue("redis://localhost:6379/0", "next_queue", namespace="myapp")

worker = Worker(
    "redis://localhost:6379/0",
    "first_queue",
    namespace="myapp",
    result_queue=next_q,
)

@worker.on("step_one")
def step_one(task):
    result = process(task)
    # 返回值自动发送到 next_q
    return {"action": "step_two", "data": result}

worker.run()
```

### CLI 命令

```bash
# 查看队列状态
python -m cli status stockev_list:fetch

# 实时监控
python -m cli watch stockev_list:fetch -i 2

# 清空队列
python -m cli clear stockev_list:fetch --force

# 重试失败任务
python -m cli retry stockev_list:fetch

# 崩溃恢复
python -m cli recover stockev_list:fetch

# 查看历史
python -m cli history stockev_list:fetch -l 50

# 启动 Dashboard
python -m cli dashboard
```

### 多阶段流水线

典型三阶段流水线：
```
stockev_list:fetch → finance:calculate → stockev_list:store
```

**启动顺序很重要（先启动下游）：**
```bash
# 终端1: store worker
python -m cli worker -q store -n stockev_list -w 4

# 终端2: calculate worker
python -m cli worker -q calculate -n finance -w 4

# 终端3: fetch worker
python -m cli worker -q fetch -n stockev_list -w 4
```

### 任务历史与归档

```python
from qtask_list.history import TaskHistory
from qtask_list.archiver import ArchiveManager, Monitor

# 查询历史
history = TaskHistory("redis://localhost:6379/0")
tasks = history.get_history("myapp:my_queue", limit=50)

# 归档到 SQLite
archiver = ArchiveManager("redis://localhost:6379/0")
count = archiver.archive_to_sqlite("myapp:my_queue", days_ago=1)

# 监控 Redis 内存
monitor = Monitor(redis_client, threshold_mb=512)
health = monitor.check_health()
```

## 队列状态说明

| 状态 | 说明 |
|------|------|
| Ready | 主队列，待处理任务 |
| Processing | 正在处理（崩溃时自动恢复） |
| Retry | 失败，等待重试 |
| DLQ | 死信队列（超过最大重试次数） |
| Delay | 延迟任务（定时执行） |

## 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `redis_url` | `redis://localhost:6379/0` | Redis 连接 |
| `namespace` | `""` | 队列命名空间 |
| `max_retry` | `3` | 最大重试次数 |
| `large_threshold` | `50KB` | 大payload阈值 |

## 参考文档

详细文档和示例：
- **`references/cli-commands.md`** - CLI 命令详解
- **`references/pipeline-example.md`** - 多阶段流水线完整示例
- **`examples/fetch_worker.py`** - Worker 示例代码
