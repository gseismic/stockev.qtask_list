import {
    canRequeue,
    compareQueues,
    formatTime,
    liveCount,
    primaryQueueIssue,
    prettyJson,
    queueActivityCount,
    shortId,
    shouldOpenTaskSamples,
    stateCount,
    stateLabel,
    states,
    summarize,
    taskState,
} from "./utils.js";

const h = React.createElement;

function Badge({ state }) {
    return h("span", { className: `badge ${state}` }, stateLabel(state));
}

export function TopBar({ health, auth, autoRefresh, lastUpdate, onRefresh, onToggleAuto, onLogout }) {
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
            auth?.enabled ? h("button", { className: "btn", onClick: onLogout, key: "logout" }, "退出") : null,
            h("span", { className: "muted", key: "updated" }, lastUpdate || "-"),
        ]),
    ]);
}

export function SystemBanner({ health }) {
    if (health?.status !== "error") return null;
    return h("div", { className: "system-banner danger" }, [
        h("strong", { key: "title" }, "连接异常，当前数据可能已过期"),
        h("span", { key: "detail" }, health.error || "无法连接 Dashboard API 或 Redis。写操作已禁用，请恢复连接后再处理任务。"),
    ]);
}

export function QueueList({ queues, selectedQueue, query, showCurrentOnly, onQuery, onToggleCurrentOnly, onSelect }) {
    const sorted = [...queues].sort(compareQueues);
    const activeQueues = sorted.filter((queue) => queueActivityCount(queue) > 0);
    const source = showCurrentOnly && activeQueues.length ? activeQueues : sorted;
    const filtered = source.filter((queue) => queue.name.toLowerCase().includes(query.toLowerCase()));

    return h("aside", { className: "sidebar" }, [
        h("div", { className: "section-title", key: "title" }, "队列"),
        h("input", {
            className: "input",
            key: "search",
            value: query,
            placeholder: "搜索队列",
            onChange: (event) => onQuery(event.target.value),
        }),
        h("div", { className: "queue-tools", key: "tools" }, [
            h("button", {
                className: `chip ${showCurrentOnly ? "active" : ""}`,
                onClick: onToggleCurrentOnly,
                key: "toggle",
            }, "只看当前"),
            h("span", { className: "muted small", key: "count" }, `${activeQueues.length}/${queues.length}`),
        ]),
        h("div", { className: "queue-list", key: "list" },
            filtered.length ? filtered.map((queue) => {
                const hasDanger = Number(queue.dlq || 0) > 0 || Number(queue.stale_workers || 0) > 0;
                const hasWork = liveCount(queue) > 0;
                return h("button", {
                    className: `queue-item ${queue.name === selectedQueue ? "active" : ""} ${hasDanger ? "has-danger" : ""} ${hasWork ? "has-work" : ""}`,
                    onClick: () => onSelect(queue.name),
                    key: queue.name,
                }, [
                    h("div", { className: "queue-name", key: "name" }, queue.name),
                    h("div", { className: "queue-counts", key: "counts" }, [
                        h("div", { className: "mini-stat", key: "ready" }, [h("strong", {}, queue.queue), h("span", {}, "待")]),
                        h("div", { className: "mini-stat", key: "proc" }, [h("strong", {}, queue.processing), h("span", {}, "中")]),
                        h("div", { className: "mini-stat", key: "retry" }, [h("strong", {}, queue.retry), h("span", {}, "重试")]),
                        h("div", { className: "mini-stat", key: "dlq" }, [h("strong", {}, queue.dlq), h("span", {}, "死信")]),
                        h("div", { className: "mini-stat", key: "delay" }, [h("strong", {}, queue.delay), h("span", {}, "延迟")]),
                    ]),
                ]);
            }) : h("div", { className: "empty compact" }, "没有队列")
        ),
    ]);
}

export function StatsGrid({ stats }) {
    const items = [
        { label: "待处理", value: stats.queue, tone: Number(stats.queue || 0) > 0 && Number(stats.active_workers || 0) === 0 ? "warning" : "" },
        { label: "处理中", value: stats.processing },
        { label: "待重试", value: stats.retry, tone: Number(stats.retry || 0) > 0 ? "warning" : "" },
        { label: "死信", value: stats.dlq, tone: Number(stats.dlq || 0) > 0 ? "danger" : "" },
        { label: "延迟", value: stats.delay },
        {
            label: "Worker",
            value: stats.active_workers,
            hint: Number(stats.stale_workers || 0) > 0
                ? `${stats.stale_workers} 失联`
                : (Number(stats.active_workers || 0) > 0 ? "活跃" : "无"),
            tone: Number(stats.stale_workers || 0) > 0 ? "danger" : "",
        },
        { label: "历史", value: stats.history, tone: "muted" },
    ];
    return h("div", { className: "stats" },
        items.map((item) => h("div", { className: `stat ${item.tone || ""}`, key: item.label }, [
            h("div", { className: "stat-value", key: "value" }, item.value ?? 0),
            h("div", { className: "stat-label", key: "label" }, item.label),
            item.hint ? h("div", { className: "stat-hint", key: "hint" }, item.hint) : null,
        ]))
    );
}

export function QueueIssueBanner({ stats }) {
    const issue = primaryQueueIssue(stats);
    return h("section", { className: `issue-banner ${issue.tone}` }, [
        h("div", { key: "copy" }, [
            h("div", { className: "issue-kicker", key: "kicker" }, "当前主要问题"),
            h("h2", { key: "title" }, issue.title),
            h("p", { key: "detail" }, issue.detail),
        ]),
        h("div", { className: "issue-next", key: "next" }, [
            h("strong", {}, "下一步"),
            h("span", {}, issue.next),
        ]),
    ]);
}

export function StateTabs({ selectedState, onState, stats }) {
    return h("div", { className: "tabs" },
        states.map((state) => h("button", {
            className: `tab ${state === selectedState ? "active" : ""}`,
            key: state,
            onClick: () => onState(state),
        }, [
            h("span", { key: "label" }, stateLabel(state)),
            h("strong", { key: "count" }, stateCount(stats, state)),
        ]))
    );
}

export function QueueActions({ queue, stats, readOnly, loading, onRetry, onRequeueDlq, onRecover }) {
    const retryDisabled = Number(stats.retry || 0) === 0;
    const dlqDisabled = Number(stats.dlq || 0) === 0;
    const processingDisabled = Number(stats.processing || 0) === 0;
    const isBusy = !!loading;

    return h("div", { className: "button-row" }, [
        h("button", {
            className: `btn${loading === "retry" ? " loading" : ""}`,
            disabled: readOnly || retryDisabled || isBusy,
            onClick: () => onRetry(queue),
            title: readOnly ? "连接异常时不能执行写操作" : (retryDisabled ? "没有待重试任务" : "将待重试任务移回待处理"),
            key: "retry",
        }, loading === "retry" ? "处理中…" : "重试队列"),
        h("button", {
            className: `btn${loading === "requeueDlq" ? " loading" : ""}`,
            disabled: readOnly || dlqDisabled || isBusy,
            onClick: () => onRequeueDlq(queue),
            title: readOnly ? "连接异常时不能执行写操作" : (dlqDisabled ? "没有死信任务" : "将死信任务移回待处理"),
            key: "dlq",
        }, loading === "requeueDlq" ? "处理中…" : "重放死信"),
        h("button", {
            className: `btn${loading === "recover" || loading === "recoverActive" ? " loading" : ""}`,
            disabled: readOnly || processingDisabled || isBusy,
            onClick: () => onRecover(queue, false),
            title: readOnly ? "连接异常时不能执行写操作" : (processingDisabled ? "没有处理中任务" : "恢复失联 Worker 的处理中任务"),
            key: "recover",
        }, loading === "recover" ? "处理中…" : "安全恢复"),
    ]);
}

export function DangerActions({ queue, stats, readOnly, loading, onRecoverActive, onClear }) {
    const processingDisabled = Number(stats.processing || 0) === 0;
    const clearDisabled = liveCount(stats) === 0;
    const isBusy = !!loading;

    return h("details", { className: "panel danger-panel" }, [
        h("summary", { className: "panel-summary danger-summary", key: "summary" }, "危险操作"),
        h("div", { className: "panel-body", key: "body" }, [
            h("p", { className: "muted action-note", key: "note" }, "这些动作会移动或删除任务，只在明确知道影响范围时使用。"),
            h("div", { className: "button-row", key: "actions" }, [
                h("button", {
                    className: `btn danger${loading === "recoverActive" ? " loading" : ""}`,
                    disabled: readOnly || processingDisabled || isBusy,
                    onClick: () => onRecoverActive(queue),
                    title: readOnly ? "连接异常时不能执行写操作" : (processingDisabled ? "没有处理中任务" : "强制恢复所有处理中任务"),
                    key: "force",
                }, loading === "recoverActive" ? "处理中…" : "强制恢复"),
                h("button", {
                    className: `btn danger${loading === "clear" ? " loading" : ""}`,
                    disabled: readOnly || clearDisabled || isBusy,
                    onClick: () => onClear(queue),
                    title: readOnly ? "连接异常时不能执行写操作" : (clearDisabled ? "当前没有可清空的任务" : "清空当前生命周期队列"),
                    key: "clear",
                }, loading === "clear" ? "处理中…" : "清空队列"),
            ]),
        ]),
    ]);
}

export function TaskToolbar({ search, onSearch, state, onState, stats }) {
    return h("div", { className: "toolbar" }, [
        h("input", {
            className: "input",
            value: search,
            placeholder: "搜索 task_id / action / payload",
            onChange: (event) => onSearch(event.target.value),
            key: "search",
        }),
        h(StateTabs, { selectedState: state, onState, stats, key: "tabs" }),
    ]);
}

export function TaskSamples({
    tasks,
    state,
    stats,
    search,
    readOnly,
    loading,
    onSearch,
    onState,
    onView,
    onRequeue,
    onDelete,
}) {
    const open = shouldOpenTaskSamples({ stats, state, search }) || undefined;
    return h("details", { className: "task-samples", open }, [
        h("summary", { className: "task-summary", key: "summary" }, [
            h("span", { key: "title" }, "任务样本"),
            h("strong", { key: "count" }, `${tasks.length} 条`),
            h("span", { className: "muted", key: "hint" }, "用于抽查 payload 和单任务处理"),
        ]),
        h("div", { className: "task-samples-body", key: "body" }, [
            h(TaskToolbar, {
                search,
                onSearch,
                state,
                onState,
                stats,
                key: "toolbar",
            }),
            h(TaskTable, {
                tasks,
                state,
                stats,
                search,
                readOnly,
                loading,
                onView,
                onRequeue,
                onDelete,
                key: "table",
            }),
        ]),
    ]);
}

function emptyTaskText({ state, stats, search }) {
    if (search) return "没有匹配任务";
    if (state === "history") return "没有历史记录";
    if (state === "all" && liveCount(stats) === 0 && Number(stats.history || 0) > 0) {
        return `当前没有任务，可切到历史查看 ${stats.history} 条记录`;
    }
    return state === "all" ? "当前没有任务" : `没有${stateLabel(state)}任务`;
}

export function TaskTable({ tasks, state, stats, search, readOnly, loading, onView, onRequeue, onDelete }) {
    if (!tasks.length) {
        return h("div", { className: "empty" }, emptyTaskText({ state, stats, search }));
    }
    const isBusy = !!loading;
    return h("div", { className: "table-wrap" },
        h("table", { className: "table" }, [
            h("thead", { key: "head" }, h("tr", {}, [
                h("th", {}, "Task ID"),
                h("th", {}, "操作"),
                h("th", {}, "状态"),
                h("th", {}, "Action"),
                h("th", {}, "Payload"),
                h("th", {}, "时间"),
            ])),
            h("tbody", { key: "body" }, tasks.map((task) => {
                const state = taskState(task);
                return h("tr", { key: `${task._source || "task"}:${task.task_id || Math.random()}` }, [
                    h("td", { className: "mono" }, shortId(task.task_id)),
                    h("td", {}, h("div", { className: "button-row row-actions" }, [
                        h("button", { className: "btn", onClick: () => onView(task), disabled: isBusy, key: "view" }, "查看"),
                        canRequeue(task) ? h("button", {
                            className: `btn${loading === "requeueTask" ? " loading" : ""}`,
                            disabled: readOnly || isBusy,
                            onClick: () => onRequeue(task),
                            title: readOnly ? "连接异常时不能执行写操作" : "重试任务",
                            key: "requeue",
                        }, loading === "requeueTask" ? "…" : "重试") : null,
                        task.task_id ? h("button", {
                            className: "btn danger",
                            disabled: readOnly || isBusy,
                            onClick: () => onDelete(task),
                            title: readOnly ? "连接异常时不能执行写操作" : "删除任务",
                            key: "delete",
                        }, "删除") : null,
                    ])),
                    h("td", {}, h(Badge, { state })),
                    h("td", {}, task.action || "-"),
                    h("td", { className: "payload-cell" }, summarize(task.payload ?? task)),
                    h("td", { className: "muted" }, formatTime(task.created_at || task.updated_at || task.run_at)),
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

export function TaskDrawer({ task, readOnly, loading, onClose, onRequeue, onDelete }) {
    if (!task) return null;
    const state = taskState(task);
    const isBusy = !!loading;
    return h(React.Fragment, {}, [
        h("div", { className: "drawer-backdrop", onClick: onClose, key: "backdrop" }),
        h("aside", { className: "drawer", key: "drawer" }, [
            h("div", { className: "drawer-header", key: "header" }, [
                h("strong", {}, shortId(task.task_id)),
                h("button", { className: "btn", onClick: onClose, disabled: isBusy }, "关闭"),
            ]),
            h("div", { className: "drawer-body", key: "body" }, [
                h("div", { className: "button-row", key: "actions" }, [
                    h(Badge, { state, key: "badge" }),
                    canRequeue(task) ? h("button", {
                        className: `btn primary${loading === "requeueTask" ? " loading" : ""}`,
                        disabled: readOnly || isBusy,
                        onClick: () => onRequeue(task),
                        title: readOnly ? "连接异常时不能执行写操作" : "重试任务",
                        key: "requeue",
                    }, loading === "requeueTask" ? "处理中…" : "重试") : null,
                    task.task_id ? h("button", {
                        className: "btn danger",
                        disabled: readOnly || isBusy,
                        onClick: () => onDelete(task),
                        title: readOnly ? "连接异常时不能执行写操作" : "删除任务",
                        key: "delete",
                    }, "删除") : null,
                ]),
                h("div", { className: "section-title", key: "payload-title" }, "Payload"),
                h("pre", { className: "json-block", key: "payload" }, prettyJson(task.payload ?? {})),
                h("div", { className: "section-title", key: "raw-title" }, "Raw"),
                h("pre", { className: "json-block", key: "raw" }, prettyJson(task)),
            ]),
        ]),
    ]);
}

export function PushTaskForm({ queue, readOnly, loading, payloadText, delaySeconds, onPayload, onDelay, onPush }) {
    const isBusy = !!loading;
    return h("details", { className: "panel push-panel" }, [
        h("summary", { className: "panel-summary", key: "summary" }, "投递任务"),
        h("div", { className: "panel-body", key: "body" }, [
            h("textarea", {
                className: "textarea",
                value: payloadText,
                placeholder: "JSON payload",
                disabled: readOnly || isBusy,
                onChange: (event) => onPayload(event.target.value),
                key: "payload",
            }),
            h("div", { className: "button-row", style: { marginTop: "10px" }, key: "row" }, [
                h("input", {
                    className: "input",
                    style: { width: "130px" },
                    type: "number",
                    min: "0",
                    title: "延迟秒数",
                    disabled: readOnly || isBusy,
                    value: delaySeconds,
                    onChange: (event) => onDelay(Number(event.target.value || 0)),
                    key: "delay",
                }),
                h("button", {
                    className: `btn primary${loading === "push" ? " loading" : ""}`,
                    disabled: readOnly || isBusy,
                    title: readOnly ? "连接异常时不能投递任务" : "投递任务",
                    onClick: () => onPush(queue),
                    key: "push",
                }, loading === "push" ? "投递中…" : "投递"),
            ]),
        ]),
    ]);
}
