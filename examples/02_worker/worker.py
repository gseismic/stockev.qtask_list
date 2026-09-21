"""用 Worker 消费任务：handler 注册、重试、错误分类的最小完整示例。

运行方式（两个终端）::

    # 终端 1：启动 Worker（自动处理 pop/ack/fail/recover）
    python examples/02_worker/worker.py

    # 终端 2：投递任务
    python examples/02_worker/producer.py

核心要点：
- @worker.on(action) 按 payload 中的 action 路由；handler 收到业务 payload
- 抛 RetryableTaskError 走自动重试（指数退避），耗尽后进 DLQ
- 抛 PermanentTaskError 不消耗重试次数，直接进 DLQ
- 未分类异常按可重试处理（code=handler_unclassified）
"""

from pathlib import Path
import random
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import Worker  # noqa: E402
from qtask_list.errors import PermanentTaskError, RetryableTaskError  # noqa: E402

REDIS_URL = "redis://localhost:6379/0"
NAMESPACE = "demo"

worker = Worker(
    REDIS_URL,
    "jobs",                 # 消费 demo:jobs
    namespace=NAMESPACE,
    max_workers=2,          # 线程池并发数
    max_attempts=3,         # 最大执行次数（含首次），耗尽进 DLQ
)


@worker.on("flaky_job")
def flaky_job(payload: dict) -> dict:
    """约 2/3 概率失败的抖动任务：演示 RetryableTaskError 与自动重试。"""
    if random.random() < 0.67:
        # 可重试错误：Worker 按 retry_backoff_base 指数退避后重新入队
        raise RetryableTaskError("transient_failure", "模拟瞬时故障（网络抖动等）")
    print(f"[flaky_job] 第 {payload.get('n')} 次尝试终于成功")
    return {"ok": True}


@worker.on("bad_job")
def bad_job(payload: dict) -> None:
    """参数错误的任务：演示 PermanentTaskError —— 不重试，直接进 DLQ。"""
    # 不可恢复错误：重试没有意义（如参数非法、目标不存在），跳过剩余次数
    raise PermanentTaskError("invalid_input", f"payload 缺少必需字段: {payload}")


@worker.on("good_job")
def good_job(payload: dict) -> dict:
    """正常任务：返回 dict 会写入历史 result 字段，便于审计。"""
    print(f"[good_job] 处理 {payload}")
    return {"echo": payload}


if __name__ == "__main__":
    # run() 阻塞运行：自动 recover 失联 Worker 的任务，注册 SIGINT/SIGTERM 优雅停机
    worker.run()
