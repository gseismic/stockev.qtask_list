# 08 Large Payload：大任务外存（RemoteStorage）

payload 超过阈值时自动上传 HTTP 存储服务，Redis 队列只存引用。

## 运行（三个终端）

```bash
# 终端 1：启动外存服务端
python -m remote_storage.server --port 8096

# 终端 2：启动消费者
python examples/08_large_payload/consumer.py

# 终端 3：生产大任务
python examples/08_large_payload/producer.py
```

需要安装 extra：`pip install -e ".[storage]"`（服务端依赖 fastapi/uvicorn）。

## 学习点

- **透明外存**：push 自动上传（>阈值才触发），pop 自动下载还原，业务代码零改动
- 队列内引用形如 `{"_large": true, "key": ...}`，可用 `qtask peek --json` 观察
- `large_threshold` 默认 50KB，本示例调小到 10KB 便于演示
- 错误分类：网络/5xx 为 `RemoteStorageTransientError`（自动退避重试）；
  对象缺失/校验和错误为永久错误（直接进 DLQ）
- 服务端按 `retain_until` 自动清理过期对象；任务重试会通过 retain 延长保留期
