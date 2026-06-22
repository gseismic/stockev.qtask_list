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
    const failed = Number(queue.failed || 0);
    const activeWorkers = Number(queue.active_workers || 0);
    const staleWorkers = Number(queue.stale_workers || 0);
    const history = Number(queue.history || 0);

    return (dlq > 0 ? 1_000_000 : 0) + Math.min(dlq, 9999)
        + (retry > 0 ? 900_000 : 0) + Math.min(retry, 9999)
        + (staleWorkers > 0 ? 800_000 : 0) + Math.min(staleWorkers, 9999)
        + (failed > 0 ? 750_000 : 0) + Math.min(failed, 9999)
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

export function isConnected(health = {}) {
    return health?.status === "ok";
}

export function hasReadyBacklogWithoutWorker(stats = {}) {
    return Number(stats.queue || 0) > 0 && Number(stats.active_workers || 0) === 0;
}

export function primaryQueueIssue(stats = {}) {
    if (Number(stats.dlq || 0) > 0) {
        return {
            tone: "danger",
            title: `死信队列有 ${stats.dlq} 个失败任务`,
            detail: "先查看失败任务的 payload 和错误原因，再决定单条重试或批量重放。",
            next: "建议先切到死信状态查看样本。",
        };
    }
    if (Number(stats.retry || 0) > 0) {
        return {
            tone: "warning",
            title: `有 ${stats.retry} 个任务等待重试`,
            detail: "这些任务已经失败过但尚未进入死信，可手动移回待处理队列。",
            next: "确认失败原因后执行重试队列。",
        };
    }
    if (Number(stats.stale_workers || 0) > 0) {
        return {
            tone: "warning",
            title: `发现 ${stats.stale_workers} 个失联 Worker`,
            detail: "失联 Worker 可能留下处理中任务，优先使用安全恢复。",
            next: "执行安全恢复前确认没有同名 Worker 仍在运行。",
        };
    }
    if (hasReadyBacklogWithoutWorker(stats)) {
        return {
            tone: "warning",
            title: `待处理队列积压 ${stats.queue} 个任务，但没有活跃 Worker`,
            detail: "当前瓶颈不是单条任务，而是没有消费者处理队列。",
            next: "启动对应 Worker，或确认业务消费者是否部署在正确的 Redis 和队列名上。",
        };
    }
    if (Number(stats.processing || 0) > 0 && Number(stats.active_workers || 0) === 0) {
        return {
            tone: "warning",
            title: `有 ${stats.processing} 个处理中任务，但没有活跃 Worker`,
            detail: "这些任务可能来自异常退出的 Worker。",
            next: "先执行安全恢复，再观察任务是否回到待处理。",
        };
    }
    return {
        tone: "ok",
        title: "当前队列未发现需要立即处理的问题",
        detail: "可以继续观察 Worker、历史和任务样本。",
        next: "保持自动刷新即可。",
    };
}

export function shouldOpenTaskSamples({ stats = {}, state = "all", search = "" }) {
    if (search) return true;
    if (state !== "all" && state !== "ready") return true;
    return !hasReadyBacklogWithoutWorker(stats);
}

export function taskState(task) {
    return task._state || task.status || "ready";
}

export function shortId(taskId) {
    if (!taskId) return "-";
    return taskId.length > 12 ? `${taskId.slice(0, 12)}...` : taskId;
}

export function summarize(value, maxLength = 140) {
    if (value && typeof value === "object" && !Array.isArray(value)) {
        if (value._large) return `[大payload] key=${value.key || "?"}`;
        if (value._compressed) return "[压缩payload]";
    }
    const text = typeof value === "string" ? value : JSON.stringify(value ?? {}, null, 0);
    if (text.length <= maxLength) return text;
    return `${text.slice(0, maxLength - 3)}...`;
}

export function extractNamespace(queueName) {
    if (!queueName || !queueName.includes(":")) return "";
    return queueName.split(":")[0];
}

export function extractNamespaces(queues) {
    const ns = new Set();
    for (const q of queues || []) {
        const name = extractNamespace(q.name);
        ns.add(name);
    }
    return [...ns].sort();
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
