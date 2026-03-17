import os
import redis
import json
from typing import Optional
from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from qtask_list.archiver import Monitor

app = FastAPI(title="qtask_list Dashboard")

# Redis 连接池
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
redis_client = redis.from_url(REDIS_URL, decode_responses=True)
monitor = Monitor(redis_client)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


def get_all_queues():
    r = redis_client
    queues = set()
    # 使用 scan_iter 代替 keys("*")
    for key in r.scan_iter("qtask:hist:*"):
        queue_name = key.replace("qtask:hist:", "")
        queues.add(queue_name)
    
    # 也可以扫描 list 类型
    for key in r.scan_iter("*"):
        if ":processing" in key or ":retry" in key or ":dlq" in key or ":delay" in key:
            continue
        if ":hist:" in key or ":task:" in key:
            continue
        try:
            if r.type(key) == "list":
                queues.add(key)
        except:
            continue
            
    return sorted(queues)


def get_queue_stats(queue_name):
    r = redis_client
    return {
        "queue": r.llen(queue_name),
        "processing": r.llen(f"{queue_name}:processing"),
        "retry": r.llen(f"{queue_name}:retry"),
        "dlq": r.llen(f"{queue_name}:dlq"),
        "delay": r.zcard(f"{queue_name}:delay"),
    }


def get_queue_tasks(queue_name: str, limit: int = 50) -> list:
    """获取队列中的任务 (从主队列和历史)"""
    r = redis_client
    results = []
    
    # 1. 从主队列获取待处理任务
    queue_msgs = r.lrange(queue_name, 0, limit - 1)
    for msg in queue_msgs:
        try:
            data = json.loads(msg)
            if isinstance(data, dict):
                payload_str = data.get("payload", "{}")
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
                task = {
                    "task_id": msg[:8] if len(msg) > 8 else msg,
                    "action": "unknown",
                    "status": "pending",
                    "_queue": queue_name,
                    "_source": "queue"
                }
            results.append(task)
        except json.JSONDecodeError:
            task = {
                "task_id": msg[:8] if len(msg) > 8 else msg,
                "action": "unknown",
                "status": "pending", 
                "_queue": queue_name,
                "_source": "queue"
            }
            results.append(task)
    
    # 2. 从 history 获取已完成的任务
    queue_task_ids = {t.get("task_id") for t in results if t.get("task_id")}
    
    idx_key = f"qtask:hist:{queue_name}"
    task_ids = r.zrevrange(idx_key, 0, limit - 1)
    
    if task_ids:
        # 1. 第一阶段：批量获取类型
        pipe = r.pipeline()
        to_fetch = [tid for tid in task_ids if tid not in queue_task_ids]
        for tid in to_fetch:
            pipe.type(f"qtask:task:{tid}")
        types = pipe.execute()
        
        # 2. 第二阶段：根据类型批量获取内容
        pipe = r.pipeline()
        for tid, rtype in zip(to_fetch, types):
            key = f"qtask:task:{tid}"
            if rtype == "hash":
                pipe.hgetall(key)
            elif rtype == "string":
                pipe.get(key)
            else:
                pipe.echo("") # 占位
        
        raw_responses = pipe.execute()
        
        for i, rtype in enumerate(types):
            data = raw_responses[i]
            if not data:
                continue
                
            task = None
            if rtype == "hash":
                task = {}
                for k, v in data.items():
                    try:
                        task[k] = json.loads(v)
                    except:
                        task[k] = v
            elif rtype == "string":
                try:
                    task = json.loads(data)
                except:
                    continue
            
            if task:
                task["_queue"] = queue_name
                task["_source"] = "history"
                results.append(task)
    
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
    r = redis_client
    key = f"qtask:task:{task_id}"
    rt = r.type(key)
    
    if rt == "hash":
        data = r.hgetall(key)
        if data:
            res = {}
            for k, v in data.items():
                try:
                    res[k] = json.loads(v)
                except:
                    res[k] = v
            return res
    else:
        raw = r.get(key)
        if raw:
            return json.loads(raw)
    return None


def get_task_by_queue(queue_name: str, limit: int = 50) -> list:
    """获取指定队列的任务"""
    r = redis_client
    idx_key = f"qtask:hist:{queue_name}"
    task_ids = r.zrevrange(idx_key, 0, limit - 1)
    
    if not task_ids:
        return []
        
    results = []
    # 1. 第一阶段
    pipe = r.pipeline()
    for tid in task_ids:
        pipe.type(f"qtask:task:{tid}")
    types = pipe.execute()
    
    # 2. 第二阶段
    pipe = r.pipeline()
    for tid, rtype in zip(task_ids, types):
        key = f"qtask:task:{tid}"
        if rtype == "hash":
            pipe.hgetall(key)
        elif rtype == "string":
            pipe.get(key)
        else:
            pipe.echo("")
            
    raw_responses = pipe.execute()
    for i, rtype in enumerate(types):
        data = raw_responses[i]
        if not data:
            continue
            
        task = None
        if rtype == "hash":
            task = {}
            for k, v in data.items():
                try:
                    task[k] = json.loads(v)
                except:
                    task[k] = v
        elif rtype == "string":
            try:
                task = json.loads(data)
            except:
                continue
        if task:
            results.append(task)
            
    return results


# ==================== API ====================

@app.get("/api/health")
def api_health():
    try:
        redis_client.ping()
        mem_info = monitor.get_memory_info()
        return {
            "status": "ok", 
            "redis": REDIS_URL,
            "memory": mem_info
        }
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
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Task not found")


@app.get("/api/actions")
def api_actions():
    """获取所有 action 类型 (安全采样)"""
    r = redis_client
    actions = set()
    # 仅采样最近的 1000 个任务
    for key in r.scan_iter("qtask:task:*", count=100):
        try:
            task = get_task_detail(key.replace("qtask:task:", ""))
            if task and task.get("action"):
                actions.add(task["action"])
            if len(actions) > 50: # 限制数量
                break
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

