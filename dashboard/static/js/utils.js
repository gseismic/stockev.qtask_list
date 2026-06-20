export const states = ["all", "ready", "processing", "retry", "dlq", "delay", "history"];

export function stateLabel(state) {
    const labels = {
        all: "全部",
        ready: "Ready",
        processing: "Processing",
        retry: "Retry",
        dlq: "DLQ",
        delay: "Delay",
        history: "History",
        active: "active",
        stale: "stale",
    };
    return labels[state] || state;
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
