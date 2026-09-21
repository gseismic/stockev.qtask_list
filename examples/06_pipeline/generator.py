"""股票数据 Pipeline 的 V2 任务生产器。

流程：``fetch_stock -> calculate_ma -> store_result``。身份、窗口和截止时间使用
``TaskSpec`` 表达：时间向下取整到分钟桶，同一分钟内重复运行会命中同一
``logical_key``，重复投递被去重拒绝而不是产生重复任务。

职责边界：生产者只负责投递第一级 ``stockev_list:fetch``；后两级由各阶段 Worker
通过 ``TaskResult.emissions`` 自动投递（见 06_pipeline/stockev/fetch_worker.py 与
06_pipeline/finance/calculate_worker.py）。运行方式见 06_pipeline/README.md。
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from qtask_list import SmartQueue, TaskSpec  # noqa: E402

REDIS_URL = "redis://localhost:6379/0"

# 命名空间
STOCKEV_NS = "stockev_list"
FINANCE_NS = "finance"


def main():
    # 入口队列：namespace + queue_name 拼出 Redis 主键 stockev_list:fetch
    fetch_q = SmartQueue(REDIS_URL, "fetch", namespace=STOCKEV_NS)

    symbols = [
        "AAPL", "TSLA", "NVDA", "MSFT", "GOOG",
        "AMZN", "META", "NFLX", "AMD", "INTC",
    ]

    # 时间向下取整到分钟作为确定性时间桶：同一分钟内重复运行会得到同一个
    # logical_key，重复投递被拒绝而不是产生重复任务。
    slot = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    # start_deadline_at = 最晚允许开始执行的时刻；pop 时已超期则拒绝执行
    # （outcome=skipped），不会产生迟到数据
    deadline = slot + timedelta(minutes=7)
    specs = [
        TaskSpec(
            # action 位于信封头，只用于 Worker 路由，不放进业务 payload
            action="fetch_stock",
            # payload 只含业务数据；这里用 URL/分区路径模拟真实抓取任务
            payload={
                "symbol": sym,
                "url": f"https://api.example.com/quote/{sym}",
                "partition": f"quotes/trade_date={slot.date().isoformat()}",
            },
            # 业务身份 = 任务类型:主体:时间桶；live 期间同键任务至多一个
            logical_key=f"pipeline:quote:{sym}:{slot.strftime('%Y%m%dT%H%MZ')}",
            # 计划时刻：纯元数据，随历史/血缘透出；不会延迟消费。
            # 需要延迟执行请用 not_before_at（见 04_delay_deadline）
            scheduled_for=slot,
            start_deadline_at=deadline,            # 执行截止
            dedup_until=slot + timedelta(days=2),  # 终态身份保留期（防重复回补）
        )
        for sym in symbols
    ]
    # enqueue_many 逐项返回 EnqueueResult，不抛异常：重复投递（duplicate_*）
    # 是正常输出而非错误，调用方按 reason 分类处理/告警
    results = fetch_q.enqueue_many(specs)
    print(f"Generating {len(specs)} stock tasks...")
    for result in results:
        # reason: enqueued / duplicate_active / duplicate_retained / superseded
        print(f"  [fetch] {result.logical_key} -> {result.reason} ({result.task_id})")

    print("\nPipeline created:")
    print(f"  {STOCKEV_NS}:fetch ({len(symbols)} tasks)")
    print(f"  {FINANCE_NS}:calculate (will be populated by fetch worker)")
    print(f"  {STOCKEV_NS}:store (will be populated by calculate worker)")
    print("\nRun workers in order:")
    print("  1. python examples/06_pipeline/stockev/store_worker.py")
    print("  2. python examples/06_pipeline/finance/calculate_worker.py")
    print("  3. python examples/06_pipeline/stockev/fetch_worker.py")


if __name__ == "__main__":
    main()
