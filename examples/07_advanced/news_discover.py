"""新闻发现与 fan-out 示例。

``discover_news`` handler 只负责发现 URL，实际抓取任务通过 ``TaskResult.emissions``
以带身份的 ``TaskSpec`` 投递到下游。这样同一 URL 在重试或重复发现时仍由队列去重。

这是「动态增长列表」的标准模式::

    news-discover（身份=调度窗口） --fan-out--> news-fetch（身份=URL 内容哈希）

发现任务随窗口产生新身份，条目任务永远复用内容身份，两层互不干扰。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import SmartQueue, TaskContext, TaskResult, TaskSpec, Worker  # noqa: E402


REDIS_URL = "redis://localhost:6379/0"
NAMESPACE = "stockev_list"

# 下游队列：每条新闻一个任务
fetch_q = SmartQueue(REDIS_URL, "news-fetch", namespace=NAMESPACE)
worker = Worker(
    REDIS_URL,
    "news-discover",          # 消费 stockev_list:news-discover
    namespace=NAMESPACE,
    result_queue=fetch_q,     # emissions 投递到 stockev_list:news-fetch
    max_workers=2,
)


def _url_key(url: str) -> str:
    """用 URL 内容生成稳定身份，避免 query 顺序之外的随机字段参与去重。"""

    # 同一 URL 无论被多少轮发现命中，哈希相同 => 只保留一个任务（或一份终态保留）
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"news:{digest}"


def _news_specs(urls: list[str], context: TaskContext) -> list[TaskSpec]:
    # 每个 URL 换成一个带身份的 TaskSpec；30 分钟截止防止堆积过久后抓取旧新闻
    now = datetime.now(timezone.utc)
    return [
        TaskSpec(
            action="fetch_news",
            payload={"url": url, "discovered_at": now.isoformat()},
            logical_key=_url_key(url),              # 条目身份 = 内容哈希
            start_deadline_at=now + timedelta(minutes=30),
            dedup_until=now + timedelta(days=7),    # 7 天内重复发现仍被去重
            parent_task_id=context.task_id,         # 血缘指向发现任务
            trace_id=context.trace_id,
        )
        for url in urls
        if url                                       # 跳过空字符串
    ]


@worker.on("discover_news")
def discover_news(payload: dict[str, Any], context: TaskContext) -> TaskResult:
    """将发现结果 fan-out 为独立的、可审计的新闻抓取任务。"""

    candidates = payload.get("urls")
    if not isinstance(candidates, list):
        # 抛 ValueError 属未分类异常：Worker 按可重试处理（耗尽后进 DLQ）
        raise ValueError("discover_news payload.urls must be a list")
    urls = [url for url in candidates if isinstance(url, str)]
    specs = _news_specs(urls, context)
    if context.stop_requested:
        # Worker 收到停止信号：协作式退出，不做大批 fan-out。
        # 这些 URL 会在下一轮发现任务中重新出现，仍由 news:<hash> 身份去重。
        return TaskResult(value={"discovered": len(specs), "emitted": 0})
    return TaskResult(
        value={"discovered": len(specs), "emitted": len(specs)},
        emissions=specs,
    )


if __name__ == "__main__":
    print(f"Starting news discovery worker for {NAMESPACE}:news-discover")
    worker.run()
