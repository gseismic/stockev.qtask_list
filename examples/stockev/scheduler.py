"""股票行情调度示例。

调度器由 cron/systemd timer 每 5 分钟运行一次。slot 由 UTC 时间向下取整得到，
因此同一时间窗口重复运行只会命中同一个 ``logical_key``，不会重复投递。
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import EnqueueResult, SmartQueue, TaskSpec  # noqa: E402


REDIS_URL = "redis://localhost:6379/0"
NAMESPACE = "stockev_list"
WATCHLIST = ("AAPL", "TSLA", "NVDA", "MSFT", "GOOG")


def floor_to_slot(now: datetime, minutes: int = 5) -> datetime:
    """把带时区时间规整到固定分钟 slot，避免调度进程启动秒数影响身份键。"""

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.replace(
        minute=(now.minute // minutes) * minutes,
        second=0,
        microsecond=0,
    )


def build_specs(
    now: datetime,
    *,
    session: str,
    symbols: Iterable[str] = WATCHLIST,
) -> list[TaskSpec]:
    """为早盘/午盘窗口创建 universe 和行情任务。"""

    if session not in {"am", "pm"}:
        raise ValueError("session must be 'am' or 'pm'")
    slot = floor_to_slot(now)
    bucket = slot.strftime("%Y%m%dT%H%MZ")
    trade_date = slot.date().isoformat()
    deadline = slot + timedelta(minutes=7)
    retained_until = slot + timedelta(days=2)
    selected_symbols = tuple(symbols)

    specs = [
        TaskSpec(
            action="discover_universe",
            payload={
                "session": session,
                "trade_date": trade_date,
                "symbols": list(selected_symbols),
            },
            logical_key=f"universe:{trade_date}-{session}",
            scheduled_for=slot,
            start_deadline_at=deadline,
            dedup_until=retained_until,
        )
    ]
    specs.extend(
        TaskSpec(
            action="fetch_quote",
            payload={
                "symbol": symbol,
                "trade_date": trade_date,
                "slot": bucket,
                "partition": f"quotes/trade_date={trade_date}",
                "session": session,
            },
            logical_key=f"quote:{symbol}:{bucket}",
            scheduled_for=slot,
            start_deadline_at=deadline,
            dedup_until=retained_until,
        )
        for symbol in selected_symbols
    )
    return specs


def main() -> None:
    parser = argparse.ArgumentParser(description="投递确定性 5 分钟股票行情任务")
    parser.add_argument("--redis-url", default=REDIS_URL)
    parser.add_argument("--namespace", default=NAMESPACE)
    parser.add_argument("--session", choices=("am", "pm"), default="am")
    args = parser.parse_args()

    queue = SmartQueue(args.redis_url, "fetch", namespace=args.namespace)
    specs = build_specs(datetime.now(timezone.utc), session=args.session)
    results: list[EnqueueResult] = queue.enqueue_many(specs)
    accepted = sum(result.accepted for result in results)
    duplicates = len(results) - accepted
    print(
        f"slot={specs[0].scheduled_for.isoformat()} session={args.session} "
        f"accepted={accepted} duplicates={duplicates}"
    )
    for result in results:
        print(result.as_dict())


if __name__ == "__main__":
    main()
