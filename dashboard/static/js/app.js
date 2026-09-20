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
    const [namespaceFilter, setNamespaceFilter] = useState(null);
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
    const [isLoading, setIsLoading] = useState(true);
    const [payloadText, setPayloadText] = useState('{\n  "symbol": "SSE:600000"\n}');
    const [delaySeconds, setDelaySeconds] = useState(0);
    const [expireSeconds, setExpireSeconds] = useState(0);
    const [pushOptions, setPushOptions] = useState({
        action: "example",
        logicalKey: "",
        scheduledFor: "",
        notBeforeAt: "",
        startDeadlineAt: "",
        dedupUntil: "",
        traceId: "",
        parentTaskId: "",
        concurrencyKey: "",
        supersedeKey: "",
        supersedeVersion: "",
        allowNew: false,
    });

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

    const updatePushOption = useCallback((name, value) => {
        setPushOptions((current) => ({ ...current, [name]: value }));
    }, []);

    function toIso(value) {
        if (!value) return null;
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime())) throw new Error(`无效时间：${value}`);
        return parsed.toISOString();
    }

    function promptFutureDeadline(message) {
        const suggested = new Date(Date.now() + 60 * 60 * 1000).toISOString();
        const value = window.prompt(message, suggested);
        if (value === null) return null;
        const parsed = new Date(value);
        if (Number.isNaN(parsed.getTime()) || parsed.getTime() <= Date.now()) {
            notify("新的最晚开始时间必须是未来的有效时间");
            return null;
        }
        return parsed.toISOString();
    }

    function taskDeadlineHasPassed(task) {
        const value = task.start_deadline_at;
        if (!value) return false;
        const milliseconds = typeof value === "number" || /^\d+(\.\d+)?$/.test(String(value))
            ? Number(value) * 1000
            : new Date(value).getTime();
        return Number.isFinite(milliseconds) && milliseconds <= Date.now();
    }

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
            setIsLoading(false);
        } catch (error) {
            setHealth({ status: "error", error: error.message });
            notify(error.message);
            setIsLoading(false);
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
            const result = await action();
            notify(typeof successMessage === "function" ? successMessage(result) : successMessage);
            await refresh();
            return result;
        } catch (error) {
            notify(error.message);
        } finally {
            setLoadingAction(null);
        }
    }

    const retryQueue = (queue) => runAction(() => api.retryQueue(queue), "retry 已移回 ready", "retry");
    const requeueDlq = (queue) => {
        if (confirmDanger("批量重放会为每个死信任务创建新 task_id，继续？")) {
            runAction(() => api.requeueDlq(queue), "DLQ 已重放为新实例", "requeueDlq");
        }
    };
    const requeueExpired = (queue) => {
        const deadline = promptFutureDeadline("为这些任务输入新的最晚开始时间（ISO 8601）：");
        if (deadline) {
            runAction(() => api.requeueExpired(queue, deadline), "超期任务已重放为新实例", "requeueExpired");
        }
    };
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
    const clearQueue = (queue, releaseIdentity = false) => {
        const message = releaseIdentity
            ? "清空队列并释放 logical identity 会允许同一业务任务再次投递，继续？"
            : "清空队列会取消 operational message，但保留 logical identity，继续？";
        if (confirmDanger(message)) {
            runAction(
                () => api.clearQueue(queue, true, false, releaseIdentity),
                releaseIdentity ? "队列已清空，身份已释放" : "队列已清空，身份仍保留",
                releaseIdentity ? "clearRelease" : "clear"
            );
        }
    };
    const deleteQueue = (queue) => {
        if (confirmDanger(`确认删除队列 ${queue}？此操作将删除所有任务数据及历史记录，不可撤销。`)) {
            runAction(() => api.deleteQueue(queue), "队列已删除", "deleteQueue");
        }
    };
    const requeueTask = (task) => {
        const fromState = taskState(task);
        const outcome = task.outcome || task.status;
        const queue = task._queue || effectiveQueue;
        if (fromState === "processing" && !confirmDanger("从 processing 重试可能影响正在运行的 Worker，继续？")) {
            return;
        }
        if (fromState === "deadline_missed") {
            const deadline = promptFutureDeadline("为重放实例输入新的最晚开始时间（ISO 8601）：");
            if (deadline) {
                runAction(
                    () => api.requeueExpired(queue, deadline, task.task_id),
                    (result) => `已创建重放实例 ${result.new_task_id || ""}`.trim(),
                    "requeueTask"
                );
            }
            return;
        }
        if (["failed", "skipped", "cancelled"].includes(outcome)) {
            let deadline = null;
            if (taskDeadlineHasPassed(task)) {
                deadline = promptFutureDeadline("原任务的最晚开始时间已过，请输入新时间（ISO 8601）：");
                if (!deadline) return;
            }
            runAction(
                () => api.replayTask(task.task_id, { queue, start_deadline_at: deadline }),
                (result) => `已创建重放实例 ${result.task_id || ""}`.trim(),
                "requeueTask"
            );
            return;
        }
        runAction(
            () => api.requeueTask(task.task_id, queue, fromState),
            (result) => result.new_task_id ? `已创建重放实例 ${result.new_task_id}` : "任务已移回待处理",
            "requeueTask"
        );
    };
    const deleteTask = (task) => {
        const queue = task._queue || effectiveQueue;
        if (confirmDanger(`删除任务 ${task.task_id}，继续？`)) {
            runAction(() => api.deleteTask(task.task_id, queue), "任务已删除", "deleteTask");
            setSelectedTask(null);
        }
    };
    const loadPayload = useCallback(async (task) => {
        const queue = task._queue || effectiveQueue;
        const state = taskState(task);
        return api.taskPayload(task.task_id, queue, state);
    }, [effectiveQueue]);
    const pushTask = (queue) => {
        try {
            const payload = JSON.parse(payloadText);
            if (!payload || Array.isArray(payload) || typeof payload !== "object") {
                throw new Error("payload 必须是 JSON object");
            }
            if (pushOptions.allowNew && !confirmDanger("ALLOW_NEW 会绕过去重并替换 logical identity owner，继续？")) {
                return;
            }
            const body = {
                payload,
                action: pushOptions.action.trim() || payload.action || null,
                delay_seconds: delaySeconds,
                expire_seconds: expireSeconds,
                logical_key: pushOptions.logicalKey.trim() || null,
                scheduled_for: toIso(pushOptions.scheduledFor),
                not_before_at: toIso(pushOptions.notBeforeAt),
                start_deadline_at: toIso(pushOptions.startDeadlineAt),
                dedup_until: toIso(pushOptions.dedupUntil),
                trace_id: pushOptions.traceId.trim() || null,
                parent_task_id: pushOptions.parentTaskId.trim() || null,
                concurrency_key: pushOptions.concurrencyKey.trim() || null,
                supersede_key: pushOptions.supersedeKey.trim() || null,
                supersede_version: pushOptions.supersedeVersion.trim() || null,
                duplicate_action: pushOptions.allowNew ? "allow_new" : "reject",
                confirm_duplicate: pushOptions.allowNew,
            };
            runAction(
                () => api.pushTask(queue, body),
                (result) => result.accepted
                    ? `任务已投递：${result.task_id}`
                    : `未投递：${result.reason}，已有 ${result.duplicate_of || "任务"}`,
                "push"
            );
        } catch (error) {
            notify(`投递参数无效：${error.message}`);
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
        skipped: 0,
        cancelled: 0,
        deadline_missed: 0,
        expired: 0,
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
        h(SystemBanner, { health, isLoading, key: "system" }),
        h("div", { className: "layout", key: "layout" }, [
            h(QueueList, {
                queues,
                selectedQueue: effectiveQueue,
                query: queueQuery,
                showCurrentOnly,
                namespaceFilter,
                onQuery: setQueueQuery,
                onToggleCurrentOnly: toggleCurrentOnly,
                onNamespaceFilter: setNamespaceFilter,
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
                                onRequeueExpired: requeueExpired,
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
                            expireSeconds,
                            options: pushOptions,
                            onPayload: setPayloadText,
                            onDelay: setDelaySeconds,
                            onExpire: setExpireSeconds,
                            onOption: updatePushOption,
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
            onLoadPayload: loadPayload,
            key: "drawer",
        }),
        toast ? h("div", { className: "toast", key: "toast" }, toast) : null,
    ]);
}

ReactDOM.createRoot(document.getElementById("root")).render(h(App));
