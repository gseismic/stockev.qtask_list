import { canRequeue, formatTime, prettyJson, shortId, stateLabel, states, summarize, taskState } from "./utils.js";

const h = React.createElement;

function Badge({ state }) {
    return h("span", { className: `badge ${state}` }, stateLabel(state));
}

export function TopBar({ health, autoRefresh, lastUpdate, onRefresh, onToggleAuto }) {
    const connected = health?.status === "ok";
    return h("header", { className: "topbar" }, [
        h("div", { className: "brand", key: "brand" }, [
            h("div", { className: "brand-mark", key: "mark" }, "Q"),
            h("div", { key: "title" }, [
                h("div", { className: "brand-title", key: "name" }, "qtask_list"),
                h("div", { className: "brand-subtitle", key: "sub" }, health?.redis || "Dashboard"),
            ]),
        ]),
        h("div", { className: "top-actions", key: "actions" }, [
            h("span", { className: `badge ${connected ? "completed" : "failed"}`, key: "health" }, connected ? "已连接" : "未连接"),
            h("button", { className: "btn", onClick: onRefresh, key: "refresh" }, "刷新"),
            h("button", { className: autoRefresh ? "btn primary" : "btn", onClick: onToggleAuto, key: "auto" }, autoRefresh ? "自动刷新" : "暂停刷新"),
            h("span", { className: "muted", key: "updated" }, lastUpdate || "-"),
        ]),
    ]);
}

export function QueueList({ queues, selectedQueue, query, onQuery, onSelect }) {
    const filtered = queues.filter((queue) => queue.name.toLowerCase().includes(query.toLowerCase()));
    return h("aside", { className: "sidebar" }, [
        h("div", { className: "section-title", key: "title" }, "队列"),
        h("input", {
            className: "input",
            key: "search",
            value: query,
            placeholder: "搜索队列",
            onChange: (event) => onQuery(event.target.value),
        }),
        h("div", { className: "queue-list", key: "list" },
            filtered.map((queue) => h("button", {
                className: `queue-item ${queue.name === selectedQueue ? "active" : ""}`,
                onClick: () => onSelect(queue.name),
                key: queue.name,
            }, [
                h("div", { className: "queue-name", key: "name" }, queue.name),
                h("div", { className: "queue-counts", key: "counts" }, [
                    h("div", { className: "mini-stat", key: "ready" }, [h("strong", {}, queue.queue), h("span", {}, "Ready")]),
                    h("div", { className: "mini-stat", key: "proc" }, [h("strong", {}, queue.processing), h("span", {}, "Proc")]),
                    h("div", { className: "mini-stat", key: "retry" }, [h("strong", {}, queue.retry), h("span", {}, "Retry")]),
                    h("div", { className: "mini-stat", key: "dlq" }, [h("strong", {}, queue.dlq), h("span", {}, "DLQ")]),
                    h("div", { className: "mini-stat", key: "delay" }, [h("strong", {}, queue.delay), h("span", {}, "Delay")]),
                ]),
            ]))
        ),
    ]);
}

export function StatsGrid({ stats }) {
    const items = [
        ["Ready", stats.queue],
        ["Processing", stats.processing],
        ["Retry", stats.retry],
        ["DLQ", stats.dlq],
        ["Delay", stats.delay],
        ["History", stats.history],
        ["Workers", stats.active_workers],
    ];
    return h("div", { className: "stats" },
        items.map(([label, value]) => h("div", { className: "stat", key: label }, [
            h("div", { className: "stat-value", key: "value" }, value ?? 0),
            h("div", { className: "stat-label", key: "label" }, label),
        ]))
    );
}

export function StateTabs({ selectedState, onState }) {
    return h("div", { className: "tabs" },
        states.map((state) => h("button", {
            className: `tab ${state === selectedState ? "active" : ""}`,
            key: state,
            onClick: () => onState(state),
        }, stateLabel(state)))
    );
}

export function QueueActions({ queue, onRetry, onRequeueDlq, onRecover, onRecoverActive, onClear }) {
    return h("div", { className: "button-row" }, [
        h("button", { className: "btn", onClick: () => onRetry(queue), key: "retry" }, "重试队列"),
        h("button", { className: "btn", onClick: () => onRequeueDlq(queue), key: "dlq" }, "重放 DLQ"),
        h("button", { className: "btn", onClick: () => onRecover(queue, false), key: "recover" }, "安全恢复"),
        h("button", { className: "btn danger", onClick: () => onRecoverActive(queue), key: "force" }, "强制恢复"),
        h("button", { className: "btn danger", onClick: () => onClear(queue), key: "clear" }, "清空"),
    ]);
}

export function TaskToolbar({ search, onSearch, state, onState }) {
    return h("div", { className: "toolbar" }, [
        h("input", {
            className: "input",
            value: search,
            placeholder: "搜索 task_id / action / payload",
            onChange: (event) => onSearch(event.target.value),
            key: "search",
        }),
        h(StateTabs, { selectedState: state, onState, key: "tabs" }),
    ]);
}

export function TaskTable({ tasks, onView, onRequeue, onDelete }) {
    if (!tasks.length) {
        return h("div", { className: "empty" }, "没有任务");
    }
    return h("div", { className: "table-wrap" },
        h("table", { className: "table" }, [
            h("thead", { key: "head" }, h("tr", {}, [
                h("th", {}, "Task ID"),
                h("th", {}, "状态"),
                h("th", {}, "Action"),
                h("th", {}, "Payload"),
                h("th", {}, "时间"),
                h("th", {}, "操作"),
            ])),
            h("tbody", { key: "body" }, tasks.map((task) => {
                const state = taskState(task);
                return h("tr", { key: `${task._source || "task"}:${task.task_id || Math.random()}` }, [
                    h("td", { className: "mono" }, shortId(task.task_id)),
                    h("td", {}, h(Badge, { state })),
                    h("td", {}, task.action || "-"),
                    h("td", { className: "payload-cell" }, summarize(task.payload ?? task)),
                    h("td", { className: "muted" }, formatTime(task.created_at || task.updated_at || task.run_at)),
                    h("td", {}, h("div", { className: "button-row" }, [
                        h("button", { className: "btn", onClick: () => onView(task), key: "view" }, "查看"),
                        canRequeue(task) ? h("button", { className: "btn", onClick: () => onRequeue(task), key: "requeue" }, "重试") : null,
                        task.task_id ? h("button", { className: "btn danger", onClick: () => onDelete(task), key: "delete" }, "删除") : null,
                    ])),
                ]);
            })),
        ])
    );
}

export function DiagnosePanel({ diagnose, workers }) {
    return h("div", { className: "panel" }, [
        h("div", { className: "panel-header", key: "header" }, h("strong", {}, "诊断")),
        h("div", { className: "panel-body", key: "body" }, [
            h("ul", { className: "diagnose-list", key: "suggestions" },
                (diagnose?.suggestions || []).map((item, index) => h("li", { key: index }, item))
            ),
            h("div", { className: "section-title", key: "worker-title" }, "Workers"),
            workers.length ? workers.map((worker) => h("div", { className: "worker-row", key: `${worker.queue}:${worker.worker_id}` }, [
                h("div", { key: "id" }, [
                    h("div", { className: "mono", key: "worker" }, worker.worker_id),
                    h("div", { className: "muted", key: "queue" }, worker.processing_key),
                ]),
                h("div", { key: "state" }, [
                    h(Badge, { state: worker.active ? "active" : "stale" }),
                    h("div", { className: "muted", key: "count" }, `${worker.processing} tasks`),
                ]),
            ])) : h("div", { className: "muted", key: "empty" }, "无 Worker"),
        ]),
    ]);
}

export function TaskDrawer({ task, onClose, onRequeue, onDelete }) {
    if (!task) return null;
    const state = taskState(task);
    return h(React.Fragment, {}, [
        h("div", { className: "drawer-backdrop", onClick: onClose, key: "backdrop" }),
        h("aside", { className: "drawer", key: "drawer" }, [
            h("div", { className: "drawer-header", key: "header" }, [
                h("strong", {}, shortId(task.task_id)),
                h("button", { className: "btn", onClick: onClose }, "关闭"),
            ]),
            h("div", { className: "drawer-body", key: "body" }, [
                h("div", { className: "button-row", key: "actions" }, [
                    h(Badge, { state, key: "badge" }),
                    canRequeue(task) ? h("button", { className: "btn primary", onClick: () => onRequeue(task), key: "requeue" }, "重试") : null,
                    task.task_id ? h("button", { className: "btn danger", onClick: () => onDelete(task), key: "delete" }, "删除") : null,
                ]),
                h("div", { className: "section-title", key: "payload-title" }, "Payload"),
                h("pre", { className: "json-block", key: "payload" }, prettyJson(task.payload ?? {})),
                h("div", { className: "section-title", key: "raw-title" }, "Raw"),
                h("pre", { className: "json-block", key: "raw" }, prettyJson(task)),
            ]),
        ]),
    ]);
}

export function PushTaskForm({ queue, payloadText, delaySeconds, onPayload, onDelay, onPush }) {
    return h("div", { className: "panel" }, [
        h("div", { className: "panel-header", key: "header" }, h("strong", {}, "投递任务")),
        h("div", { className: "panel-body", key: "body" }, [
            h("textarea", {
                className: "textarea",
                value: payloadText,
                onChange: (event) => onPayload(event.target.value),
                key: "payload",
            }),
            h("div", { className: "button-row", style: { marginTop: "10px" }, key: "row" }, [
                h("input", {
                    className: "input",
                    style: { width: "130px" },
                    type: "number",
                    min: "0",
                    value: delaySeconds,
                    onChange: (event) => onDelay(Number(event.target.value || 0)),
                    key: "delay",
                }),
                h("button", { className: "btn primary", onClick: () => onPush(queue), key: "push" }, "投递"),
            ]),
        ]),
    ]);
}
