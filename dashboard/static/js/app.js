import { api } from "./api.js";
import {
    DiagnosePanel,
    PushTaskForm,
    QueueActions,
    QueueList,
    StatsGrid,
    TaskDrawer,
    TaskTable,
    TaskToolbar,
    TopBar,
} from "./components.js";
import { confirmDanger, taskState } from "./utils.js";

const h = React.createElement;
const { useCallback, useEffect, useMemo, useState } = React;

function App() {
    const [health, setHealth] = useState({ status: "loading" });
    const [queues, setQueues] = useState([]);
    const [selectedQueue, setSelectedQueue] = useState("");
    const [queueQuery, setQueueQuery] = useState("");
    const [state, setState] = useState("all");
    const [taskSearch, setTaskSearch] = useState("");
    const [tasks, setTasks] = useState([]);
    const [diagnose, setDiagnose] = useState(null);
    const [workers, setWorkers] = useState([]);
    const [selectedTask, setSelectedTask] = useState(null);
    const [autoRefresh, setAutoRefresh] = useState(true);
    const [lastUpdate, setLastUpdate] = useState("");
    const [toast, setToast] = useState("");
    const [payloadText, setPayloadText] = useState('{\n  "action": "example"\n}');
    const [delaySeconds, setDelaySeconds] = useState(0);

    const selectedStats = useMemo(
        () => queues.find((queue) => queue.name === selectedQueue) || queues[0] || null,
        [queues, selectedQueue]
    );

    const effectiveQueue = selectedQueue || selectedStats?.name || "";

    const notify = useCallback((message) => {
        setToast(message);
        window.setTimeout(() => setToast(""), 2500);
    }, []);

    const loadQueues = useCallback(async () => {
        const data = await api.queues();
        setQueues(data);
        if (!effectiveQueue && data.length) {
            setSelectedQueue(data[0].name);
        }
        return data;
    }, [effectiveQueue]);

    const loadQueueDetail = useCallback(async (queue = effectiveQueue) => {
        if (!queue) {
            setTasks([]);
            setDiagnose(null);
            setWorkers([]);
            return;
        }
        const [taskData, diagnoseData, workerData] = await Promise.all([
            api.queueTasks({ queue, state, search: taskSearch, limit: 120 }),
            api.diagnose(queue),
            api.workers(queue),
        ]);
        setTasks(taskData.tasks || []);
        setDiagnose(diagnoseData);
        setWorkers(workerData);
    }, [effectiveQueue, state, taskSearch]);

    const refresh = useCallback(async () => {
        try {
            const healthData = await api.health();
            setHealth(healthData);
            await loadQueues();
            await loadQueueDetail();
            setLastUpdate(new Date().toLocaleTimeString());
        } catch (error) {
            setHealth({ status: "error", error: error.message });
            notify(error.message);
        }
    }, [loadQueueDetail, loadQueues, notify]);

    useEffect(() => {
        refresh();
    }, []);

    useEffect(() => {
        loadQueueDetail();
    }, [effectiveQueue, state, taskSearch]);

    useEffect(() => {
        if (!autoRefresh) return undefined;
        const timer = window.setInterval(refresh, 3000);
        return () => window.clearInterval(timer);
    }, [autoRefresh, refresh]);

    async function runAction(action, successMessage) {
        try {
            await action();
            notify(successMessage);
            await refresh();
        } catch (error) {
            notify(error.message);
        }
    }

    const retryQueue = (queue) => runAction(() => api.retryQueue(queue), "retry 已移回 ready");
    const requeueDlq = (queue) => runAction(() => api.requeueDlq(queue), "DLQ 已重放");
    const recoverQueue = (queue, includeActive) => runAction(
        () => api.recoverQueue(queue, includeActive),
        includeActive ? "已强制恢复 processing" : "已恢复 stale processing"
    );
    const recoverActive = (queue) => {
        if (confirmDanger("强制恢复可能抢回活跃 Worker 正在处理的任务，继续？")) {
            recoverQueue(queue, true);
        }
    };
    const clearQueue = (queue) => {
        if (confirmDanger("清空队列会删除 ready/processing/retry/delay/DLQ，继续？")) {
            runAction(() => api.clearQueue(queue, true, false), "队列已清空");
        }
    };
    const requeueTask = (task) => {
        const fromState = taskState(task);
        const queue = task._queue || effectiveQueue;
        if (fromState === "processing" && !confirmDanger("从 processing 重试可能影响正在运行的 Worker，继续？")) {
            return;
        }
        runAction(() => api.requeueTask(task.task_id, queue, fromState), "任务已重试");
    };
    const deleteTask = (task) => {
        const queue = task._queue || effectiveQueue;
        if (confirmDanger(`删除任务 ${task.task_id}，继续？`)) {
            runAction(() => api.deleteTask(task.task_id, queue), "任务已删除");
            setSelectedTask(null);
        }
    };
    const pushTask = (queue) => {
        try {
            const payload = JSON.parse(payloadText);
            runAction(() => api.pushTask(queue, payload, delaySeconds), "任务已投递");
        } catch (error) {
            notify(`Payload JSON 无效：${error.message}`);
        }
    };

    const stats = selectedStats || {
        queue: 0,
        processing: 0,
        retry: 0,
        dlq: 0,
        delay: 0,
        history: 0,
        active_workers: 0,
    };

    return h("div", { className: "app" }, [
        h(TopBar, {
            health,
            autoRefresh,
            lastUpdate,
            onRefresh: refresh,
            onToggleAuto: () => setAutoRefresh((value) => !value),
            key: "topbar",
        }),
        h("div", { className: "layout", key: "layout" }, [
            h(QueueList, {
                queues,
                selectedQueue: effectiveQueue,
                query: queueQuery,
                onQuery: setQueueQuery,
                onSelect: setSelectedQueue,
                key: "queues",
            }),
            h("main", { className: "main", key: "main" }, effectiveQueue ? [
                h(StatsGrid, { stats, key: "stats" }),
                h("div", { className: "split", key: "split" }, [
                    h("section", { className: "panel", key: "tasks" }, [
                        h("div", { className: "panel-header", key: "header" }, [
                            h("div", { className: "queue-heading", key: "heading" }, [
                                h("h1", {}, effectiveQueue),
                                h("p", {}, "任务生命周期"),
                            ]),
                            h(QueueActions, {
                                queue: effectiveQueue,
                                onRetry: retryQueue,
                                onRequeueDlq: requeueDlq,
                                onRecover: recoverQueue,
                                onRecoverActive: recoverActive,
                                onClear: clearQueue,
                                key: "actions",
                            }),
                        ]),
                        h("div", { className: "panel-body", key: "body" }, [
                            h(TaskToolbar, {
                                search: taskSearch,
                                onSearch: setTaskSearch,
                                state,
                                onState: setState,
                                key: "toolbar",
                            }),
                            h(TaskTable, {
                                tasks,
                                onView: setSelectedTask,
                                onRequeue: requeueTask,
                                onDelete: deleteTask,
                                key: "table",
                            }),
                        ]),
                    ]),
                    h("div", { key: "side" }, [
                        h(DiagnosePanel, { diagnose, workers, key: "diagnose" }),
                        h("div", { style: { height: "14px" }, key: "gap" }),
                        h(PushTaskForm, {
                            queue: effectiveQueue,
                            payloadText,
                            delaySeconds,
                            onPayload: setPayloadText,
                            onDelay: setDelaySeconds,
                            onPush: pushTask,
                            key: "push",
                        }),
                    ]),
                ]),
            ] : h("div", { className: "empty" }, "没有队列")),
        ]),
        h(TaskDrawer, {
            task: selectedTask,
            onClose: () => setSelectedTask(null),
            onRequeue: requeueTask,
            onDelete: deleteTask,
            key: "drawer",
        }),
        toast ? h("div", { className: "toast", key: "toast" }, toast) : null,
    ]);
}

ReactDOM.createRoot(document.getElementById("root")).render(h(App));
