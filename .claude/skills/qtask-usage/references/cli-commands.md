# CLI 命令详解

## 队列管理命令

### status - 查看队列状态

```bash
# 查看所有队列状态
python -m cli status

# 查看指定队列状态
python -m cli status stockev_list:fetch
```

输出示例：
```
Queue: stockev_list:fetch
  Ready: 10
  Processing: 2
  Retry: 3
  DLQ: 1
  Delay: 5
```

### watch - 实时监控

```bash
# 每2秒刷新
python -m cli watch stockev_list:fetch -i 2

# 指定 Redis URL
python -m cli watch stockev_list:fetch -i 2 -r redis://localhost:6379/0
```

### clear - 清空队列

```bash
# 清空主队列
python -m cli clear stockev_list:fetch --force

# 清空所有相关队列（retry, dlq, delay）
python -m cli clear stockev_list:fetch --all --force
```

## 任务管理命令

### retry - 重试失败任务

```bash
# 将 retry 队列任务移回主队列
python -m cli retry stockev_list:fetch

# 同时清理已过期任务
python -m cli retry stockev_list:fetch --clean
```

### requeue - 重队列 DLQ

```bash
# 将 DLQ 任务重新入队
python -m cli requeue stockev_list:fetch --force
```

### recover - 崩溃恢复

```bash
# 恢复 processing 队列中的任务到主队列
python -m cli recover stockev_list:fetch
```

## 历史命令

### history - 查看任务历史

```bash
# 查看队列历史
python -m cli history stockev_list:fetch -l 50

# 查看单个任务详情
python -m cli history -t <task_id>

# 按状态筛选
python -m cli history stockev_list:fetch -s success
python -m cli history stockev_list:fetch -s failed
```

## Worker 命令

### worker - 启动 Worker

```bash
# 基本用法
python -m cli worker -q fetch -n stockev_list -w 4

# 指定 Redis URL
python -m cli worker -q fetch -n stockev_list -w 4 -r redis://localhost:6379/0

# 开启日志
python -m cli worker -q fetch -n stockev_list -w 4 -v
```

参数说明：
- `-q, --queue`: 队列名称
- `-n, --namespace`: 命名空间
- `-w, --workers`: 工作线程数
- `-r, --redis`: Redis URL

## Dashboard 命令

### dashboard - 启动 Web 面板

```bash
# 默认端口 8765
python -m cli dashboard

# 指定端口
python -m cli dashboard -p 8080

# 指定 Redis
python -m cli dashboard -r redis://localhost:6379/0
```

## 归档命令

### archive - 归档历史

```bash
# 归档1天前的历史
python -m cli archive --days 1

# 归档指定队列
python -m cli archive stockev_list:fetch --days 7
```

### clean-history - 清理过期历史

```bash
# 清理15天前的历史
python -m cli clean-history stockev_list:fetch -t 15
```

## 监控命令

### monitor - 监控 Redis 内存

```bash
# 查看内存使用
python -m cli monitor

# 指定阈值 (MB)
python -m cli monitor -t 512
```
