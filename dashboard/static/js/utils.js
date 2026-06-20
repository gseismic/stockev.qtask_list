export const states = ["all", "ready", "processing", "retry", "dlq", "delay", "history"];

export function stateLabel(state) {
    const labels = {
        all: "当前",
        ready: "待处理",
        processing: "处理中",
        retry: "待重试",
        dlq: "死信",
        delay: "延迟",
        history: "历史",
        active: "活跃",
        stale: "失联",
    };
    return labels[state] || state;
}

export function liveCount(stats = {}) {
    return Number(stats.queue || 0)
        + Number(stats.processing || 0)
        + Number(stats.retry || 0)
        + Number(stats.dlq || 0)
        + Number(stats.delay || 0);
}

export function queueActivityCount(queue = {}) {
    return liveCount(queue) + Number(queue.active_workers || 0) + Number(queue.stale_workers || 0);
}

export function stateCount(stats = {}, state = "all") {
    if (state === "all") return liveCount(stats);
    if (state === "ready") return Number(stats.queue || 0);
    return Number(stats[state] || 0);
}

export function queuePriority(queue = {}) {
    const ready = Number(queue.queue || 0);
    const processing = Number(queue.processing || 0);
    const retry = Number(queue.retry || 0);
    const dlq = Number(queue.dlq || 0);
    const delay = Number(queue.delay || 0);
    const activeWorkers = Number(queue.active_workers || 0);
    const staleWorkers = Number(queue.stale_workers || 0);
    const history = Number(queue.history || 0);

    return (dlq > 0 ? 1_000_000 : 0) + Math.min(dlq, 9999)
        + (retry > 0 ? 900_000 : 0) + Math.min(retry, 9999)
        + (staleWorkers > 0 ? 800_000 : 0) + Math.min(staleWorkers, 9999)
        + (processing > 0 ? 700_000 : 0) + Math.min(processing, 9999)
        + (ready > 0 ? 500_000 : 0) + Math.min(ready, 9999)
        + (delay > 0 ? 300_000 : 0) + Math.min(delay, 9999)
        + (activeWorkers > 0 ? 100_000 : 0) + Math.min(activeWorkers, 999)
        + Math.min(history, 99);
}

export function compareQueues(a, b) {
    const priorityDiff = queuePriority(b) - queuePriority(a);
    if (priorityDiff !== 0) return priorityDiff;
    return String(a.name || "").localeCompare(String(b.name || ""));
}

export function taskState(task) {
    return task._state || task.status || "ready";
}

export function shortId(taskId) {
    if (!taskId) return "-";
    return taskId.length > 12 ? `${taskId.slice(0, 12)}...` : taskId;
}

export function summarize(value, maxLength = 140) {
    const text = typeof value === "string" ? value : JSON.stringify(value ?? {}, null, 0);
    if (text.length <= maxLength) return text;
    return `${text.slice(0, maxLength - 3)}...`;
}

export function prettyJson(value) {
    return JSON.stringify(value, null, 2);
}

export function formatTime(timestamp) {
    if (!timestamp) return "-";
    const date = new Date(Number(timestamp) * 1000);
    if (Number.isNaN(date.getTime())) return "-";
    return date.toLocaleString();
}

export function canRequeue(task) {
    return ["retry", "dlq", "delay", "processing"].includes(taskState(task));
}

export function confirmDanger(message) {
    return window.confirm(message);
}
