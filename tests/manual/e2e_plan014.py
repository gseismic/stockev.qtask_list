"""PLAN-014 端到端验证：真实 Worker 循环跑三个场景（Redis db15，用后清理）"""
import json
import threading
import time

import redis

from qtask_list import SmartQueue, Worker

URL = "redis://localhost:6379/15"
r = redis.from_url(URL, decode_responses=True)
r.flushdb()

results = []
fail_counts = {"AAPL": 0}

worker = Worker(URL, "quote", namespace="e2e", max_workers=2, max_retry=3,
                stale_recover_interval=30)


@worker.on("fetch_quote")
def fetch_quote(task):
    symbol = task["symbol"]
    if symbol == "AAPL" and fail_counts["AAPL"] < 2:
        fail_counts["AAPL"] += 1
        raise ConnectionError("模拟数据源故障")
    results.append((symbol, "_retry" in task))
    return None


@worker.on("discover")
def discover(task):
    # 场景3 fan-out：发现 2 条新闻，逐条投递并带身份键去重
    news_q = SmartQueue(URL, "news", namespace="e2e")
    news_q.push_batch(
        [{"action": "fetch_news", "url": "https://x/a"}, {"action": "fetch_news", "url": "https://x/b"}],
        logical_keys=["news:a", "news:b"],
    )


news_results = []
news_worker = Worker(URL, "news", namespace="e2e")


@news_worker.on("fetch_news")
def fetch_news(task):
    news_results.append(task["url"])


t1 = threading.Thread(target=worker.run, daemon=True)
t2 = threading.Thread(target=news_worker.run, daemon=True)
t1.start()
t2.start()
time.sleep(1)

# ---- 场景2：定时抓取（同桶去重 + 过期跳过）----
bucket = time.strftime("%Y%m%dT%H%M")
quote_q = SmartQueue(URL, "quote", namespace="e2e")
assert quote_q.push({"action": "fetch_quote", "symbol": "TSLA"},
                    logical_key=f"quote:TSLA:{bucket}", expire_seconds=120) is not None
assert quote_q.push({"action": "fetch_quote", "symbol": "TSLA"},
                     logical_key=f"quote:TSLA:{bucket}", expire_seconds=120) is None  # 同桶去重
# AAPL 会失败两次后成功（验证退避路径经 delay 再回主队列）
quote_q.push({"action": "fetch_quote", "symbol": "AAPL"}, logical_key=f"quote:AAPL:{bucket}")
# 过期任务：投递时已过执行截止（把信封改到过去）
tid_exp = quote_q.push({"action": "fetch_quote", "symbol": "MSFT"},
                       logical_key=f"quote:MSFT:{bucket}", expire_seconds=120)
msg = r.lindex("e2e:quote", 0)
data = json.loads(msg)
data["expires_at"] = time.time() - 1
r.lset("e2e:quote", 0, json.dumps(data))

# 退避默认 30s，等不起：把 delay 里的 AAPL 提前到期（加速验证）
time.sleep(2)
deadline = time.time() + 20
while time.time() < deadline:
    members = r.zrange("e2e:quote:delay", 0, -1)
    if members:
        r.zadd("e2e:quote:delay", {m: time.time() - 1 for m in members})
    if any(sym == "AAPL" and retried for sym, retried in results):
        break
    time.sleep(0.5)

# ---- 场景3：动态列表 fan-out（发现任务 → 子任务 + 去重）----
quote_q.push({"action": "discover", "symbol": "*"})
time.sleep(3)

worker.stop()
news_worker.stop()
t1.join(timeout=5)
t2.join(timeout=5)

print("quote 结果:", results)
print("news 结果:", news_results)
print("AAPL 失败次数:", fail_counts["AAPL"])
print("MSFT(过期) 历史状态:", quote_q.history.get(tid_exp))
print("delay 残留:", r.zcard("e2e:quote:delay"))
print("DLQ:", r.llen("e2e:quote:dlq"), "retry:", r.llen("e2e:quote:retry"))

assert ("TSLA", False) in results, "TSLA 应成功执行"
assert ("AAPL", True) in results, "AAPL 应经过退避重试后成功（handler 可见 _retry）"
assert not any(s == "MSFT" for s, _ in results), "过期的 MSFT 不应被执行"
assert quote_q.history.get(tid_exp)["status"] == "skipped", "MSFT 应标记 skipped"
assert sorted(news_results) == ["https://x/a", "https://x/b"], "fan-out 子任务应被执行"
assert r.llen("e2e:quote:dlq") == 0
assert r.llen("e2e:news") == 0, "news 队列应消费完"

r.flushdb()
print("\n端到端验证全部通过 ✓")
