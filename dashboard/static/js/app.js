import { api } from "./api.js";
import {
    DangerActions,
    DiagnosePanel,
    GlobalOverview,
    PushTaskForm,
    QueueActions,
    QueueIssueBanner,
    QueueList,
    StatsGrid,
    SystemBanner,
    TaskDrawer,
    TaskSamples,
    TopBar,
} from "./components.js";
import { compareQueues, confirmDanger, isConnected, queueActivityCount, taskState } from "./utils.js";

const h = React.createElement;
const { useCallback, useEffect, useMemo, useState } = React;

function chooseQueue(queues) {
    const sorted = [...queues].sort(compareQueues);
    return sorted.find((queue) => queueActivityCount(queue) > 0) || sorted[0] || null;
}

function App() {
    const [authInfo, setAuthInfo] = useState({ enabled: false, authenticated: true });
    const [health, setHealth] = useState({ status: "loading" });
    const [queues, setQueues] = useState([]);
    const [selectedQueue, setSelectedQueue] = useState("");
    const [queueQuery, setQueueQuery] = useState("");
    const [showCurrentOnly, setShowCurrentOnly] = useState(true);
    const [state, setState] = useState("all");
    const [taskSearch, setTaskSearch] = useState("");
    const [tasks, setTasks] = useState([]);
    const [diagnose, setDiagnose] = useState(null);
    const [workers, setWorkers] = useState([]);
    const [selectedTask, setSelectedTask] = useState(null);
    const [autoRefresh, setAutoRefresh] = useState(true);
    const [lastUpdate, setLastUpdate] = useState("");
    const [toast, setToast] = useState("");
    const [loadingAction, setLoadingAction] = useState(null);
    const [payloadText, setPayloadText] = useState('{\n  "action": "example"\n}');
    const [delaySeconds, setDelaySeconds] = useState(0);

    const rankedQueues = useMemo(() => [...queues].sort(compareQueues), [queues]);

    const selectedStats = useMemo(() => (
        rankedQueues.find((queue) => queue.name === selectedQueue)
            || chooseQueue(rankedQueues)
            || null
    ), [rankedQueues, selectedQueue]);

    const effectiveQueue = selectedQueue || selectedStats?.name || "";
    const readOnly = !isConnected(health);

    const notify = useCallback((message) => {
        setToast(message);
        window.setTimeout(() => setToast(""), 2500);
    }, []);

    const loadQueues = useCallback(async () => {
        const data = await api.queues();
        setQueues(data);
        setSelectedQueue((current) => {
            const currentStats = data.find((queue) => queue.name === current);
            const hasActiveQueues = data.some((queue) => queueActivityCount(queue) > 0);
            if (
                currentStats
                && (!showCurrentOnly || queueActivityCount(currentStats) > 0 || !hasActiveQueues)
            ) {
                return current;
            }
            return chooseQueue(data)?.name || "";
        });
        return data;
    }, [showCurrentOnly]);

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
            const authData = await api.auth();
            setAuthInfo(authData);
            if (authData.enabled && !authData.authenticated) {
                window.location.href = "/login";
                return;
            }
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
        loadQueueDetail().catch((error) => {
            setHealth((value) => ({ ...value, status: "error", error: error.message }));
            notify(error.message);
        });
    }, [effectiveQueue, loadQueueDetail, notify, state, taskSearch]);

    useEffect(() => {
        if (!autoRefresh) return undefined;
        const timer = window.setInterval(refresh, 3000);
        return () => window.clearInterval(timer);
    }, [autoRefresh, refresh]);

    async function runAction(action, successMessage, actionName) {
        setLoadingAction(actionName || null);
        try {
            await action();
            notify(successMessage);
            await refresh();
        } catch (error) {
            notify(error.message);
        } finally {
            setLoadingAction(null);
        }
    }

    const retryQueue = (queue) => runAction(() => api.retryQueue(queue), "retry 已移回 ready", "retry");
    const requeueDlq = (queue) => runAction(() => api.requeueDlq(queue), "DLQ 已重放", "requeueDlq");
    const recoverQueue = (queue, includeActive) => runAction(
        () => api.recoverQueue(queue, includeActive),
        includeActive ? "已强制恢复 processing" : "已恢复 stale processing",
        includeActive ? "recoverActive" : "recover"
    );
    const recoverActive = (queue) => {
        if (confirmDanger("强制恢复可能抢回活跃 Worker 正在处理的任务，继续？")) {
            recoverQueue(queue, true);
        }
    };
    const clearQueue = (queue) => {
        if (confirmDanger("清空队列会删除 ready/processing/retry/delay/DLQ，继续？")) {
            runAction(() => api.clearQueue(queue, true, false), "队列已清空", "clear");
        }
    };
    const deleteQueue = (queue) => {
        if (confirmDanger(`确认删除队列 ${queue}？此操作将删除所有任务数据及历史记录，不可撤销。`)) {
            runAction(() => api.deleteQueue(queue), "队列已删除", "deleteQueue");
        }
    };
    const requeueTask = (task) => {
        const fromState = taskState(task);
        const queue = task._queue || effectiveQueue;
        if (fromState === "processing" && !confirmDanger("从 processing 重试可能影响正在运行的 Worker，继续？")) {
            return;
        }
        runAction(() => api.requeueTask(task.task_id, queue, fromState), "任务已重试", "requeueTask");
    };
    const deleteTask = (task) => {
        const queue = task._queue || effectiveQueue;
        if (confirmDanger(`删除任务 ${task.task_id}，继续？`)) {
            runAction(() => api.deleteTask(task.task_id, queue), "任务已删除", "deleteTask");
            setSelectedTask(null);
        }
    };
    const pushTask = (queue) => {
        try {
            const payload = JSON.parse(payloadText);
            runAction(() => api.pushTask(queue, payload, delaySeconds), "任务已投递", "push");
        } catch (error) {
            notify(`Payload JSON 无效：${error.message}`);
        }
    };
    const logout = async () => {
        await api.logout();
        window.location.href = authInfo.enabled ? "/login" : "/";
    };
    const toggleCurrentOnly = () => {
        const nextValue = !showCurrentOnly;
        setShowCurrentOnly(nextValue);
        if (nextValue && selectedStats && queueActivityCount(selectedStats) === 0) {
            setSelectedQueue(chooseQueue(queues)?.name || selectedQueue);
        }
    };

    const stats = selectedStats || {
        queue: 0,
        processing: 0,
        retry: 0,
        dlq: 0,
        delay: 0,
        history: 0,
        completed: 0,
        failed: 0,
        active_workers: 0,
    };

    return h("div", { className: "app" }, [
        h(TopBar, {
            health,
            auth: authInfo,
            autoRefresh,
            lastUpdate,
            onRefresh: refresh,
            onToggleAuto: () => setAutoRefresh((value) => !value),
            onLogout: logout,
            key: "topbar",
        }),
        h(SystemBanner, { health, key: "system" }),
        h("div", { className: "layout", key: "layout" }, [
            h(QueueList, {
                queues,
                selectedQueue: effectiveQueue,
                query: queueQuery,
                showCurrentOnly,
                onQuery: setQueueQuery,
                onToggleCurrentOnly: toggleCurrentOnly,
                onSelect: setSelectedQueue,
                onDelete: deleteQueue,
                key: "queues",
            }),
            h("main", { className: "main", key: "main" }, effectiveQueue ? [
                h("div", { className: "context-label", key: "context" }, "当前队列状态"),
                h(GlobalOverview, { queues, key: "overview" }),
                h(StatsGrid, { stats, key: "stats" }),
                h(QueueIssueBanner, { stats, key: "issue" }),
                h("div", { className: "split", key: "split" }, [
                    h("section", { className: "panel", key: "tasks" }, [
                        h("div", { className: "panel-header", key: "header" }, [
                            h("div", { className: "queue-heading", key: "heading" }, [
                                h("h1", {}, effectiveQueue),
                                h("p", {}, "任务生命周期"),
                            ]),
                            h(QueueActions, {
                                queue: effectiveQueue,
                                stats,
                                readOnly,
                                loading: loadingAction,
                                onRetry: retryQueue,
                                onRequeueDlq: requeueDlq,
                                onRecover: recoverQueue,
                                key: "actions",
                            }),
                        ]),
                        h("div", { className: "panel-body", key: "body" }, [
                            h(TaskSamples, {
                                tasks,
                                state,
                                stats,
                                search: taskSearch,
                                readOnly,
                                loading: loadingAction,
                                onSearch: setTaskSearch,
                                onState: setState,
                                onView: setSelectedTask,
                                onRequeue: requeueTask,
                                onDelete: deleteTask,
                                key: "samples",
                            }),
                        ]),
                    ]),
                    h("div", { key: "side" }, [
                        h(DiagnosePanel, { diagnose, workers, key: "diagnose" }),
                        h("div", { style: { height: "14px" }, key: "gap" }),
                        h(DangerActions, {
                            queue: effectiveQueue,
                            stats,
                            readOnly,
                            loading: loadingAction,
                            onRecoverActive: recoverActive,
                            onClear: clearQueue,
                            key: "danger",
                        }),
                        h("div", { style: { height: "14px" }, key: "danger-gap" }),
                        h(PushTaskForm, {
                            queue: effectiveQueue,
                            readOnly,
                            loading: loadingAction,
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
            readOnly,
            loading: loadingAction,
            onClose: () => setSelectedTask(null),
            onRequeue: requeueTask,
            onDelete: deleteTask,
            key: "drawer",
        }),
        toast ? h("div", { className: "toast", key: "toast" }, toast) : null,
    ]);
}

ReactDOM.createRoot(document.getElementById("root")).render(h(App));
