"""大 payload 外存（RemoteStorage）：超过阈值自动上传 HTTP 存储服务。

需要三个终端（需要本地 Redis）::

    # 终端 1：启动外存服务端（qtask_list[storage] 依赖）
    python -m remote_storage.server --port 8096

    # 终端 2：启动消费者
    python examples/08_large_payload/consumer.py

    # 终端 3：生产大任务
    python examples/08_large_payload/producer.py

机制：
- push 时 payload 序列化后超过 large_threshold（默认 50KB）→ 自动 POST 上传到
  RemoteStorage 服务，队列里只存 {"_large": true, "key": ...} 引用；
- pop 时检测到引用 → 自动 GET 下载还原完整 payload，消费方无感知；
- 服务端按 retain_until 自动清理过期对象（默认 TTL 见服务端 --ttl-days）。
"""

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import RemoteStorage, SmartQueue  # noqa: E402

REDIS_URL = "redis://localhost:6379/0"
NAMESPACE = "demo"
QUEUE_NAME = "big-payload"

# 客户端指向外存服务；生产环境应使用 HTTPS 与鉴权
storage = RemoteStorage("http://localhost:8096")

# 阈值调小到 10KB 方便演示（生产默认 50KB）
queue = SmartQueue(
    REDIS_URL,
    QUEUE_NAME,
    namespace=NAMESPACE,
    storage=storage,
    large_threshold=10 * 1024,
)
