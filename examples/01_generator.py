"""
分布式爬虫 - 爬取任务生成器
在每个爬虫节点上运行，负责推送爬取任务
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qtask_list import SmartQueue

REDIS_URL = "redis://localhost:6379/0"
NAMESPACE = "stockev_list"

fetch_q = SmartQueue(REDIS_URL, "fetch", namespace=NAMESPACE)

symbols = [
    "AAPL", "TSLA", "NVDA", "MSFT", "GOOG", "AMZN", "META", "NFLX",
    "AMD", "INTC", "BABA", "JD", "PDD", "NIO", "XPEV", "LI",
]

print(f"Pushing {len(symbols)} tasks to {NAMESPACE}:fetch")

for sym in symbols:
    task_id = fetch_q.push({
        "action": "fetch_stock",
        "symbol": sym,
        "url": f"https://api.example.com/quote/{sym}"
    })
    print(f"  queued: {sym} (id: {task_id[:8]}...)")

print("Done!")
