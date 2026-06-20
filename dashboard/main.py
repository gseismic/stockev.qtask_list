import os
from typing import Any, Dict, Optional

import redis
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from qtask_list.admin import QueueAdmin, QueueState
from qtask_list.archiver import Monitor


app = FastAPI(title="qtask_list Dashboard")

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
redis_client: Any = redis.from_url(REDIS_URL, decode_responses=True)
admin = QueueAdmin(redis_url=REDIS_URL, redis_client=redis_client)
monitor = Monitor(redis_client)

BASE_DIR = os.path.dirname(__file__)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class PushTaskRequest(BaseModel):
    payload: Dict[str, Any]
    delay_seconds: int = Field(default=0, ge=0)


class RecoverRequest(BaseModel):
    include_active: bool = False


class ClearQueueRequest(BaseModel):
    include_dlq: bool = True
    include_history: bool = False


class RequeueTaskRequest(BaseModel):
    queue: str
    from_state: QueueState = QueueState.dlq


class RequeueDlqRequest(BaseModel):
    task_id: Optional[str] = None


@app.get("/api/health")
def api_health():
    try:
        redis_client.ping()
        mem_info = monitor.get_memory_info()
        return {"status": "ok", "redis": REDIS_URL, "memory": mem_info}
    except Exception as e:
        return {"status": "error", "error": str(e), "redis": REDIS_URL}


@app.get("/api/queues")
def api_queues():
    return admin.list_queues()


@app.get("/api/workers")
def api_workers(queue: Optional[str] = Query(None, description="Queue name")):
    return admin.list_workers(queue)


@app.get("/api/actions")
def api_actions(limit: int = Query(1000, ge=1, le=10000)):
    actions = set()
    scanned = 0
    for key in redis_client.scan_iter("qtask:task:*", count=100):
        task_id = key.replace("qtask:task:", "")
        task = admin.get_task(task_id)
        if task and task.get("action"):
            actions.add(str(task["action"]))
        scanned += 1
        if scanned >= limit:
            break
    return sorted(actions)


@app.get("/api/queue/{name}")
def api_queue(name: str):
    return {"name": name, "stats": admin.queue_stats(name), "workers": admin.list_workers(name)}


@app.get("/api/queue/{name}/tasks")
def api_queue_tasks(
    name: str,
    state: QueueState = Query(QueueState.all, description="Task state"),
    search: Optional[str] = Query(None, description="Search in task data"),
    limit: int = Query(50, ge=1, le=500),
):
    tasks = admin.list_tasks(name, state=state, limit=limit, search=search)
    return {"queue": name, "state": state.value, "tasks": tasks, "count": len(tasks)}


@app.get("/api/queue/{name}/diagnose")
def api_queue_diagnose(name: str):
    return admin.diagnose(name)


@app.post("/api/queue/{name}/tasks")
def api_push_task(name: str, request: PushTaskRequest):
    return admin.push_task(name, request.payload, delay_seconds=request.delay_seconds)


@app.post("/api/queue/{name}/retry")
def api_retry_queue(name: str):
    return admin.move_retry(name)


@app.post("/api/queue/{name}/requeue-dlq")
def api_requeue_dlq(name: str, request: RequeueDlqRequest):
    return admin.requeue_dlq(name, task_id=request.task_id)


@app.post("/api/queue/{name}/recover")
def api_recover_queue(name: str, request: RecoverRequest):
    return admin.recover(name, include_active=request.include_active)


@app.post("/api/queue/{name}/clear")
def api_clear_queue(name: str, request: ClearQueueRequest):
    return admin.clear_queue(
        name,
        include_dlq=request.include_dlq,
        include_history=request.include_history,
    )


@app.get("/api/tasks")
def api_tasks(
    queue: Optional[str] = Query(None, description="Queue name"),
    status: Optional[str] = Query(None, description="State/status filter"),
    action: Optional[str] = Query(None, description="Filter by action"),
    search: Optional[str] = Query(None, description="Search in task data"),
    limit: int = Query(50, ge=1, le=500),
):
    queues = [queue] if queue else [item["name"] for item in admin.list_queues()]
    tasks: list[Dict[str, Any]] = []
    selected_state = QueueState(status) if status in QueueState._value2member_map_ else QueueState.all

    for queue_name in queues:
        remaining = max(limit - len(tasks), 0)
        if remaining <= 0:
            break
        for task in admin.list_tasks(queue_name, state=selected_state, limit=remaining, search=search):
            if action and task.get("action") != action:
                continue
            tasks.append(task)
            if len(tasks) >= limit:
                break

    return {
        "tasks": tasks,
        "count": len(tasks),
        "filters": {"queue": queue, "status": status, "action": action, "search": search},
    }


@app.get("/api/task/{task_id}")
def api_task(task_id: str):
    task = admin.get_task(task_id)
    if task:
        return task
    raise HTTPException(status_code=404, detail="Task not found")


@app.post("/api/task/{task_id}/requeue")
def api_task_requeue(task_id: str, request: RequeueTaskRequest):
    result = admin.requeue_task(request.queue, task_id, request.from_state)
    if result["moved"] == 0:
        raise HTTPException(status_code=404, detail="Task not found in requested state")
    return result


@app.delete("/api/task/{task_id}")
def api_task_delete(
    task_id: str,
    queue: Optional[str] = Query(None, description="Limit deletion to one queue"),
):
    return admin.delete_task(task_id, queue_name=queue)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8765))
    uvicorn.run(app, host="0.0.0.0", port=port)
