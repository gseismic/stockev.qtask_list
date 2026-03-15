import os
import redis
import json
from typing import Optional
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI(title="qtask_list Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def get_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)


def get_all_queues():
    r = get_redis()
    queues = set()
    keys = r.keys("*")
    for key in keys:
        if not isinstance(key, str):
            continue
        if ":processing" in key or ":retry" in key or ":dlq" in key or ":delay" in key:
            continue
        if ":hist:" in key or ":task:" in key:
            continue
        if r.type(key) == "list":
            queues.add(key)
    return sorted(queues)


def get_queue_stats(queue_name):
    r = get_redis()
    return {
        "queue": r.llen(queue_name),
        "processing": r.llen(f"{queue_name}:processing"),
        "retry": r.llen(f"{queue_name}:retry"),
        "dlq": r.llen(f"{queue_name}:dlq"),
        "delay": r.zcard(f"{queue_name}:delay"),
    }


def get_queue_tasks(queue_name: str, limit: int = 50) -> list:
    """获取队列中的任务 (从主队列和历史)"""
    r = get_redis()
    results = []
    
    # 1. 从主队列获取待处理任务
    queue_msgs = r.lrange(queue_name, 0, -1)
    for msg in queue_msgs:
        try:
            # 尝试解析为 JSON
            data = json.loads(msg)
            if isinstance(data, dict):
                payload_str = data.get("payload", "{}")
                # payload 可能是 JSON 字符串
                try:
                    payload = json.loads(payload_str) if isinstance(payload_str, str) else payload_str
                except:
                    payload = {}
                task = {
                    "task_id": data.get("task_id", ""),
                    "action": payload.get("action", "") if isinstance(payload, dict) else "unknown",
                    "status": "pending",
                    "_queue": queue_name,
                    "_source": "queue",
                    "_raw": data
                }
            else:
                # 简单字符串
                task = {
                    "task_id": msg[:8] if len(msg) > 8 else msg,
                    "action": "unknown",
                    "status": "pending",
                    "_queue": queue_name,
                    "_source": "queue"
                }
            results.append(task)
        except json.JSONDecodeError:
            # 非 JSON 格式
            task = {
                "task_id": msg[:8] if len(msg) > 8 else msg,
                "action": "unknown",
                "status": "pending", 
                "_queue": queue_name,
                "_source": "queue"
            }
            results.append(task)
    
    # 2. 从 history 获取已完成的任务（排除已在队列中的任务）
    queue_task_ids = {t.get("task_id") for t in results if t.get("task_id")}
    
    idx_key = f"qtask:hist:{queue_name}"
    task_ids = r.zrevrange(idx_key, 0, limit - 1)
    
    for tid in task_ids:
        if tid in queue_task_ids:
            continue
        key = f"qtask:task:{tid}"
        raw = r.get(key)
        if raw:
            try:
                task = json.loads(raw)
                task["_queue"] = queue_name
                task["_source"] = "history"
                results.append(task)
            except:
                continue
    
    return results[:limit]


def get_all_tasks(
    queue: Optional[str] = None,
    status: Optional[str] = None,
    action: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 50,
) -> list:
    """获取任务列表，支持条件筛选"""
    results = []
    
    queues = [queue] if queue else get_all_queues()
    
    for q in queues:
        tasks = get_queue_tasks(q, limit=limit)
        
        for task in tasks:
            # 筛选条件
            if status and task.get("status") != status:
                continue
            if action and task.get("action") != action:
                continue
            if search:
                search_lower = search.lower()
                task_str = json.dumps(task).lower()
                if search_lower not in task_str:
                    continue
            
            task["_queue"] = q
            results.append(task)
            
            if len(results) >= limit:
                break
        
        if len(results) >= limit:
            break
    
    return results[:limit]


def get_task_detail(task_id: str) -> Optional[dict]:
    """获取任务详情"""
    r = get_redis()
    key = f"qtask:task:{task_id}"
    raw = r.get(key)
    if raw:
        return json.loads(raw)
    return None


def get_task_by_queue(queue_name: str, limit: int = 50) -> list:
    """获取指定队列的任务"""
    r = get_redis()
    idx_key = f"qtask:hist:{queue_name}"
    task_ids = r.zrevrange(idx_key, 0, limit - 1)
    
    results = []
    for tid in task_ids:
        key = f"qtask:task:{tid}"
        raw = r.get(key)
        if raw:
            results.append(json.loads(raw))
    return results


# ==================== API ====================

@app.get("/api/health")
def api_health():
    try:
        r = get_redis()
        r.ping()
        return {"status": "ok", "redis": REDIS_URL}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/api/queues")
def api_queues():
    queues = get_all_queues()
    result = []
    for q in queues:
        stats = get_queue_stats(q)
        result.append({"name": q, **stats})
    return result


@app.get("/api/queue/{name}")
def api_queue(name: str):
    stats = get_queue_stats(name)
    tasks = get_task_by_queue(name)
    return {"name": name, "stats": stats, "tasks": tasks}


@app.get("/api/tasks")
def api_tasks(
    queue: Optional[str] = Query(None, description="Queue name"),
    status: Optional[str] = Query(None, description="Filter by status"),
    action: Optional[str] = Query(None, description="Filter by action"),
    search: Optional[str] = Query(None, description="Search in task data"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
):
    """获取任务列表，支持条件筛选"""
    tasks = get_all_tasks(queue=queue, status=status, action=action, search=search, limit=limit)
    return {
        "tasks": tasks,
        "count": len(tasks),
        "filters": {"queue": queue, "status": status, "action": action, "search": search}
    }


@app.get("/api/task/{task_id}")
def api_task(task_id: str):
    task = get_task_detail(task_id)
    if task:
        return task
    return {"error": "Task not found"}, 404


@app.get("/api/actions")
def api_actions():
    """获取所有 action 类型"""
    r = get_redis()
    actions = set()
    task_keys = r.keys("qtask:task:*")
    for key in task_keys[:1000]:
        raw = r.get(key)
        if raw:
            try:
                task = json.loads(raw)
                if task.get("action"):
                    actions.add(task["action"])
            except:
                continue
    return sorted([a for a in actions if a])


# ==================== HTML ====================

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8765))
    uvicorn.run(app, host="0.0.0.0", port=port)
