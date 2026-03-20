# CLI Commands Reference

## Queue Management

### status - View Queue Status

```bash
# View all queues
python -m cli status

# View specific queue
python -m cli status stockev_list:fetch
```

Output:
```
Queue: stockev_list:fetch
  Ready: 10
  Processing: 2
  Retry: 3
  DLQ: 1
  Delay: 5
```

### watch - Real-time Monitoring

```bash
# Refresh every 2 seconds
python -m cli watch stockev_list:fetch -i 2

# With custom Redis
python -m cli watch stockev_list:fetch -i 2 -r redis://localhost:6379/0
```

### clear - Clear Queue

```bash
# Clear main queue only
python -m cli clear stockev_list:fetch --force

# Clear all (retry, dlq, delay)
python -m cli clear stockev_list:fetch --all --force
```

---

## Task Management

### retry - Retry Failed Tasks

```bash
# Move retry queue to main
python -m cli retry stockev_list:fetch

# With cleanup
python -m cli retry stockev_list:fetch --clean
```

### requeue - Re-enqueue DLQ

```bash
# Move DLQ tasks back to main queue
python -m cli requeue stockev_list:fetch --force
```

### recover - Crash Recovery

```bash
# Move processing tasks to main queue
python -m cli recover stockev_list:fetch
```

---

## History Commands

### history - Query Task History

```bash
# List recent tasks
python -m cli history stockev_list:fetch -l 50

# Single task detail
python -m cli history -t <task_id>

# Filter by status
python -m cli history stockev_list:fetch -s success
python -m cli history stockev_list:fetch -s failed
```

---

## Worker Commands

### worker - Start Worker

```bash
# Basic worker with 4 threads
python -m cli worker -q fetch -n stockev_list -w 4

# Full options
python -m cli worker -q fetch -n stockev_list -w 4 -r redis://localhost:6379/0 -v
```

Parameters:
| Flag | Description |
|------|-------------|
| `-q, --queue` | Queue name |
| `-n, --namespace` | Namespace |
| `-w, --workers` | Number of worker threads |
| `-r, --redis` | Redis URL |
| `-v, --verbose` | Enable logging |

---

## Dashboard

### dashboard - Launch Web UI

```bash
# Default port 8765
python -m cli dashboard

# Custom port
python -m cli dashboard -p 8080

# Custom Redis
python -m cli dashboard -r redis://localhost:6379/0
```

Access at: `http://localhost:8765`

---

## Archive Commands

### archive - Archive History

```bash
# Archive 1 day ago
python -m cli archive --days 1

# Archive specific queue
python -m cli archive stockev_list:fetch --days 7
```

### clean-history - Clean Expired

```bash
# Clean 15 days old
python -m cli clean-history stockev_list:fetch -t 15
```

---

## Monitoring

### monitor - Redis Memory

```bash
# Basic
python -m cli monitor

# Custom threshold (MB)
python -m cli monitor -t 512
```