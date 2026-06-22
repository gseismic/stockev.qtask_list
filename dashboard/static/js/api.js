async function request(path, options = {}) {
    const response = await fetch(path, {
        ...options,
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {}),
        },
    });

    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        if (response.status === 401 && path !== "/api/login") {
            const next = `${window.location.pathname}${window.location.search}`;
            window.location.href = `/login?next=${encodeURIComponent(next)}`;
        }
        const detail = data.detail || response.statusText;
        throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return data;
}

const queuePath = (queue) => encodeURIComponent(queue);

export const api = {
    auth: () => request("/api/auth"),
    login: (username, password) =>
        request("/api/login", {
            method: "POST",
            body: JSON.stringify({ username, password }),
        }),
    logout: () => request("/api/logout", { method: "POST" }),
    health: () => request("/api/health"),
    queues: () => request("/api/queues"),
    workers: (queue = "") => request(`/api/workers${queue ? `?queue=${queuePath(queue)}` : ""}`),
    diagnose: (queue) => request(`/api/queue/${queuePath(queue)}/diagnose`),
    queueTasks: ({ queue, state, search, limit = 100, createdAfter, createdBefore, completedAfter, completedBefore }) => {
        const params = new URLSearchParams({ state, limit: String(limit) });
        if (search) params.set("search", search);
        if (createdAfter) params.set("created_after", String(createdAfter));
        if (createdBefore) params.set("created_before", String(createdBefore));
        if (completedAfter) params.set("completed_after", String(completedAfter));
        if (completedBefore) params.set("completed_before", String(completedBefore));
        return request(`/api/queue/${queuePath(queue)}/tasks?${params.toString()}`);
    },
    pushTask: (queue, payload, delaySeconds = 0, expireSeconds = 0) =>
        request(`/api/queue/${queuePath(queue)}/tasks`, {
            method: "POST",
            body: JSON.stringify({ payload, delay_seconds: delaySeconds, expire_seconds: expireSeconds }),
        }),
    retryQueue: (queue) => request(`/api/queue/${queuePath(queue)}/retry`, { method: "POST" }),
    requeueDlq: (queue, taskId = null) =>
        request(`/api/queue/${queuePath(queue)}/requeue-dlq`, {
            method: "POST",
            body: JSON.stringify({ task_id: taskId }),
        }),
    requeueExpired: (queue, taskId = null) =>
        request(`/api/queue/${queuePath(queue)}/requeue-expired`, {
            method: "POST",
            body: JSON.stringify({ task_id: taskId }),
        }),
    recoverQueue: (queue, includeActive = false) =>
        request(`/api/queue/${queuePath(queue)}/recover`, {
            method: "POST",
            body: JSON.stringify({ include_active: includeActive }),
        }),
    clearQueue: (queue, includeDlq = true, includeHistory = false) =>
        request(`/api/queue/${queuePath(queue)}/clear`, {
            method: "POST",
            body: JSON.stringify({ include_dlq: includeDlq, include_history: includeHistory }),
        }),
    deleteQueue: (queue) =>
        request(`/api/queue/${queuePath(queue)}`, { method: "DELETE" }),
    taskPayload: (taskId, queue, state = "all") => {
        const params = new URLSearchParams({ queue, state });
        return request(`/api/task/${encodeURIComponent(taskId)}/payload?${params.toString()}`);
    },
    requeueTask: (taskId, queue, fromState) =>
        request(`/api/task/${encodeURIComponent(taskId)}/requeue`, {
            method: "POST",
            body: JSON.stringify({ queue, from_state: fromState }),
        }),
    deleteTask: (taskId, queue = "") => {
        const suffix = queue ? `?queue=${queuePath(queue)}` : "";
        return request(`/api/task/${encodeURIComponent(taskId)}${suffix}`, { method: "DELETE" });
    },
};
