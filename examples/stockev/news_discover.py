"""新闻发现与 fan-out 示例。

``discover_news`` handler 只负责发现 URL，实际抓取任务通过 ``TaskResult.emissions``
以带身份的 ``TaskSpec`` 投递到下游。这样同一 URL 在重试或重复发现时仍由队列去重。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import SmartQueue, TaskContext, TaskResult, TaskSpec, Worker  # noqa: E402


REDIS_URL = "redis://localhost:6379/0"
NAMESPACE = "stockev_list"

fetch_q = SmartQueue(REDIS_URL, "news-fetch", namespace=NAMESPACE)
worker = Worker(
    REDIS_URL,
    "news-discover",
    namespace=NAMESPACE,
    result_queue=fetch_q,
    max_workers=2,
)


def _url_key(url: str) -> str:
    """用 URL 内容生成稳定身份，避免 query 顺序之外的随机字段参与去重。"""

    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"news:{digest}"


def _news_specs(urls: list[str], context: TaskContext) -> list[TaskSpec]:
    now = datetime.now(timezone.utc)
    return [
        TaskSpec(
            action="fetch_news",
            payload={"url": url, "discovered_at": now.isoformat()},
            logical_key=_url_key(url),
            start_deadline_at=now + timedelta(minutes=30),
            dedup_until=now + timedelta(days=7),
            parent_task_id=context.task_id,
            trace_id=context.trace_id,
        )
        for url in urls
        if url
    ]


@worker.on("discover_news")
def discover_news(payload: dict[str, Any], context: TaskContext) -> TaskResult:
    """将发现结果 fan-out 为独立的、可审计的新闻抓取任务。"""

    candidates = payload.get("urls")
    if not isinstance(candidates, list):
        raise ValueError("discover_news payload.urls must be a list")
    urls = [url for url in candidates if isinstance(url, str)]
    specs = _news_specs(urls, context)
    if context.stop_requested:
        return TaskResult(value={"discovered": len(specs), "emitted": 0})
    return TaskResult(
        value={"discovered": len(specs), "emitted": len(specs)},
        emissions=specs,
    )


if __name__ == "__main__":
    print(f"Starting news discovery worker for {NAMESPACE}:news-discover")
    worker.run()
