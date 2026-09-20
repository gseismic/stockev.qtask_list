import {
    canRequeue,
    compareQueues,
    descriptorPayload,
    extractNamespace,
    extractNamespaces,
    formatTime,
    liveCount,
    primaryQueueIssue,
    prettyJson,
    queueActivityCount,
    retryFamilyCount,
    shortId,
    shouldOpenTaskSamples,
    stateCount,
    stateLabel,
    states,
    summarize,
    taskFailure,
    taskOutcome,
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

export function SystemBanner({ health, isLoading }) {
    if (isLoading) {
        return h("div", { className: "system-banner loading" }, [
            h("strong", { key: "title" }, "正在加载…"),
            h("span", { key: "detail" }, "正在获取队列和任务数据，请稍候。"),
        ]);
    }
    if (health?.status !== "error") return null;
    return h("div", { className: "system-banner danger" }, [
        h("strong", { key: "title" }, "连接异常，当前数据可能已过期"),
        h("span", { key: "detail" }, health.error || "无法连接 Dashboard API 或 Redis。写操作已禁用，请恢复连接后再处理任务。"),
    ]);
}

export function QueueList({ queues, selectedQueue, query, showCurrentOnly, namespaceFilter, onQuery, onToggleCurrentOnly, onNamespaceFilter, onSelect, onDelete }) {
    const sorted = [...queues].sort(compareQueues);
    const activeQueues = sorted.filter((queue) => queueActivityCount(queue) > 0);
    const source = showCurrentOnly && activeQueues.length ? activeQueues : sorted;
    const byNamespace = namespaceFilter === null
        ? source
        : source.filter((queue) => extractNamespace(queue.name) === namespaceFilter);
    const filtered = byNamespace.filter((queue) => queue.name.toLowerCase().includes(query.toLowerCase()));
    const namespaces = extractNamespaces(source);

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
            }, "有活动的队列"),
            h("span", { className: "muted small", key: "count" }, `${activeQueues.length}/${queues.length}`),
        ]),
        namespaces.length > 1 ? h("div", { className: "namespace-filter", key: "ns" }, [
            h("button", {
                className: `ns-chip ${namespaceFilter === null ? "active" : ""}`,
                onClick: () => onNamespaceFilter(null),
                key: "ns-all",
            }, "全部"),
            ...namespaces.map((ns) => h("button", {
                className: `ns-chip ${namespaceFilter === ns ? "active" : ""}`,
                onClick: () => onNamespaceFilter(ns),
                key: `ns-${ns}`,
            }, ns || "无命名空间")),
        ]) : null,
        h("div", { className: "queue-list", key: "list" },
            filtered.length ? filtered.map((queue) => {
                const hasDanger = Number(queue.dlq || 0) > 0 || Number(queue.stale_workers || 0) > 0;
                const hasWork = liveCount(queue) > 0;
                const completed = Number(queue.completed || 0);
                const failed = Number(queue.failed || 0);
                const isEmpty = liveCount(queue) === 0 && Number(queue.history || 0) === 0 && Number(queue.active_workers || 0) === 0;
                return h("button", {
                    className: `queue-item ${queue.name === selectedQueue ? "active" : ""} ${hasDanger ? "has-danger" : ""} ${hasWork ? "has-work" : ""}`,
                    onClick: () => onSelect(queue.name),
                    key: queue.name,
                }, [
                    h("div", { className: "queue-name-row", key: "namerow" }, [
                        h("div", { className: "queue-name", key: "name" }, queue.name),
                        isEmpty ? h("span", {
                            className: "queue-delete-btn",
                            title: "删除此队列",
                            onClick: (event) => { event.stopPropagation(); onDelete(queue.name); },
                            key: "del",
                        }, "×") : null,
                    ]),
                    h("div", { className: "queue-counts", key: "counts" }, [
                        h("div", { className: "mini-stat", key: "ready" }, [h("strong", {}, queue.queue), h("span", {}, "待")]),
                        h("div", { className: "mini-stat", key: "proc" }, [h("strong", {}, queue.processing), h("span", {}, "中")]),
                        h("div", { className: "mini-stat", key: "retry" }, [h("strong", {}, retryFamilyCount(queue)), h("span", {}, "重试")]),
                        h("div", { className: "mini-stat", key: "dlq" }, [h("strong", {}, queue.dlq), h("span", {}, "死信")]),
                        h("div", { className: "mini-stat", key: "delay" }, [h("strong", {}, queue.delay), h("span", {}, "延迟")]),
                    ]),
                    h("div", { className: "queue-outcome", key: "outcome" }, [
                        h("span", { className: "outcome-ok", key: "ok" }, `✓ ${completed}`),
                        h("span", { className: `outcome-fail ${failed > 0 ? "has-fail" : ""}`, key: "fail" }, `✗ ${failed}`),
                        Number(queue.cancelled || 0) > 0
                            ? h("span", { className: "muted", key: "cancelled" }, `⊘ ${queue.cancelled}`)
                            : null,
                        Number(queue.skipped || 0) > 0
                            ? h("span", { className: "muted", key: "skipped" }, `↷ ${queue.skipped}`)
                            : null,
                        Number(queue.deadline_missed || 0) > 0
                            ? h("span", { className: "outcome-fail has-fail", key: "missed" }, `⏰ ${queue.deadline_missed}`)
                            : null,
                    ]),
                ]);
            }) : h("div", { className: "empty compact" }, "没有队列")
        ),
    ]);
}

export function GlobalOverview({ queues }) {
    if (!queues || queues.length === 0) return null;
    const totals = queues.reduce((acc, q) => ({
        queue: acc.queue + Number(q.queue || 0),
        processing: acc.processing + Number(q.processing || 0),
        retry: acc.retry + retryFamilyCount(q),
        dlq: acc.dlq + Number(q.dlq || 0),
        delay: acc.delay + Number(q.delay || 0),
        completed: acc.completed + Number(q.completed || 0),
        failed: acc.failed + Number(q.failed || 0),
        cancelled: acc.cancelled + Number(q.cancelled || 0),
        deadline_missed: acc.deadline_missed + Number(q.deadline_missed || 0),
    }), { queue: 0, processing: 0, retry: 0, dlq: 0, delay: 0, completed: 0, failed: 0, cancelled: 0, deadline_missed: 0 });

    const items = [
        { label: "待处理", value: totals.queue, tone: totals.queue > 0 ? "warn" : "" },
        { label: "处理中", value: totals.processing },
        { label: "已完成", value: totals.completed, tone: totals.completed > 0 ? "ok" : "" },
        { label: "已失败", value: totals.failed, tone: totals.failed > 0 ? "fail" : "" },
        { label: "错过截止", value: totals.deadline_missed, tone: totals.deadline_missed > 0 ? "warn" : "" },
        { label: "已取消", value: totals.cancelled },
        { label: "重试", value: totals.retry },
        { label: "死信", value: totals.dlq, tone: totals.dlq > 0 ? "fail" : "" },
    ].filter(item => item.value > 0 || item.tone);

    // totals.retry 同时包含 retry_wait，而 retry_wait 已经属于 delay，不能重复计算。
    const totalLive = queues.reduce((sum, queue) => sum + liveCount(queue), 0);
    const totalDone = totals.completed + totals.failed;

    return h("div", { className: "global-overview" }, [
        h("span", { className: "overview-title", key: "title" }, "跨队列总览"),
        h("div", { className: "overview-items", key: "items" },
            items.map((item) => h("span", { className: `overview-item ${item.tone}`, key: item.label }, [
                h("span", { className: "overview-label", key: "label" }, item.label),
                h("strong", { key: "value" }, item.value),
            ]))
        ),
        totalLive > 0 || totalDone > 0 ? h("span", { className: "overview-summary", key: "summary" },
            `进行中 ${totalLive} / 已完成 ${totalDone}`
        ) : null,
    ]);
}

export function StatsGrid({ stats }) {
    const items = [
        { label: "待处理", value: stats.queue, tone: Number(stats.queue || 0) > 0 && Number(stats.active_workers || 0) === 0 ? "warning" : "" },
        { label: "处理中", value: stats.processing },
        { label: "已完成", value: stats.completed, tone: Number(stats.completed || 0) > 0 ? "ok" : "" },
        { label: "已失败", value: stats.failed, tone: Number(stats.failed || 0) > 0 ? "danger" : "" },
        { label: "待重试", value: retryFamilyCount(stats), tone: retryFamilyCount(stats) > 0 ? "warning" : "" },
        { label: "死信", value: stats.dlq, tone: Number(stats.dlq || 0) > 0 ? "danger" : "" },
        { label: "错过截止", value: stats.deadline_missed, tone: Number(stats.deadline_missed || 0) > 0 ? "warning" : "" },
        { label: "已取消", value: stats.cancelled },
        { label: "延迟", value: stats.delay },
        {
            label: "Worker",
            value: stats.active_workers,
            hint: Number(stats.stale_workers || 0) > 0
                ? `${stats.stale_workers} 失联`
                : (Number(stats.active_workers || 0) > 0 ? "活跃" : "无"),
            tone: Number(stats.stale_workers || 0) > 0 ? "danger" : "",
        },
        { label: "历史", value: stats.history, hint: "含已完成与已失败", tone: "muted" },
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

export function QueueActions({ queue, stats, readOnly, loading, onRetry, onRequeueDlq, onRecover, onRequeueExpired }) {
    const retryDisabled = Number(stats.retry || 0) === 0;
    const dlqDisabled = Number(stats.dlq || 0) === 0;
    const processingDisabled = Number(stats.processing || 0) === 0;
    const expiredDisabled = Number(stats.deadline_missed || 0) === 0;
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
        h("button", {
            className: `btn${loading === "requeueExpired" ? " loading" : ""}`,
            disabled: readOnly || expiredDisabled || isBusy,
            onClick: () => onRequeueExpired(queue),
            title: readOnly ? "连接异常时不能执行写操作" : (expiredDisabled ? "没有错过截止的任务" : "以新 task_id 和新截止时间重放"),
            key: "expired",
        }, loading === "requeueExpired" ? "处理中…" : "重放超期"),
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
                h("button", {
                    className: `btn danger${loading === "clearRelease" ? " loading" : ""}`,
                    disabled: readOnly || clearDisabled || isBusy,
                    onClick: () => onClear(queue, true),
                    title: readOnly ? "连接异常时不能执行写操作" : (clearDisabled ? "当前没有可清空的任务" : "清空队列并释放 logical identity"),
                    key: "clear-release",
                }, loading === "clearRelease" ? "处理中…" : "清空并释放身份"),
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
                h("th", {}, "发布时间"),
                h("th", {}, "完成时间"),
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
                    h("td", { className: "payload-cell" }, summarize(descriptorPayload(task))),
                    h("td", { className: "muted" }, formatTime(task.created_at)),
                    h("td", { className: "muted" }, ["completed", "failed", "skipped", "cancelled"].includes(task.status || task.outcome) ? formatTime(task.updated_at || task.finished_at) : "-"),
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

export function TaskDrawer({ task, readOnly, loading, onClose, onRequeue, onDelete, onLoadPayload }) {
    const [resolvedPayload, setResolvedPayload] = React.useState(null);
    const [payloadLoading, setPayloadLoading] = React.useState(false);
    const [payloadNote, setPayloadNote] = React.useState("");

    React.useEffect(() => {
        setResolvedPayload(null);
        setPayloadNote("");
        if (!task || !onLoadPayload) return;
        const payload = descriptorPayload(task);
        // 需要走 resolve API 的情形：payload 缺失（V2 历史）或 payload 是压缩/外存引用。
        const isRef = payload && typeof payload === "object"
            && (payload._large || payload._compressed);
        const isMissing = payload === null || payload === undefined;
        if (!isRef && !isMissing) return;

        setPayloadLoading(true);
        onLoadPayload(task).then((result) => {
            if (result.payload !== undefined && result.payload !== null) {
                setResolvedPayload(result.payload);
            }
            if (result._note) setPayloadNote(result._note);
        }).catch((e) => {
            setPayloadNote(e.message || "加载失败");
        }).finally(() => setPayloadLoading(false));
    }, [task, onLoadPayload]);

    if (!task) return null;
    const state = taskState(task);
    const failure = taskFailure(task);
    const isBusy = !!loading;

    // 用户可读的核心字段；其余运行时字段继续留在「内部元数据」。
    const userFields = [
        "task_id",
        "action",
        "status",
        "outcome",
        "attempt",
        "max_attempts",
        "reason_code",
        "reason",
        "logical_key",
        "scheduled_for",
        "start_deadline_at",
        "dedup_until",
        "trace_id",
        "parent_task_id",
        "replay_of",
        "replayed_by",
        "concurrency_key",
        "supersede_key",
        "supersede_version",
        "created_at",
        "updated_at",
        "finished_at",
        "result",
    ];
    const userData = {};
    const metaData = {};
    for (const [key, value] of Object.entries(task)) {
        if (userFields.includes(key)) {
            userData[key] = value;
        } else {
            metaData[key] = value;
        }
    }
    if (failure) {
        userData.last_error_code = failure.code;
        userData.last_error_reason = failure.reason;
    }

    const displayPayload = resolvedPayload !== null ? resolvedPayload : descriptorPayload(task);
    const payloadIsRef = displayPayload && typeof displayPayload === "object"
        && (displayPayload._large || displayPayload._compressed);
    const payloadMissing = displayPayload === null || displayPayload === undefined;

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
                failure ? h("div", { className: "failure-block", key: "failure" }, [
                    h("div", { className: "failure-title", key: "title" }, [
                        "失败原因 ",
                        h("code", { key: "code" }, failure.code || "unknown"),
                    ]),
                    failure.reason
                        ? h("div", { className: "failure-reason", key: "reason" }, failure.reason)
                        : null,
                ]) : null,
                h("div", { className: "section-title", key: "payload-title" }, "Payload"),
                payloadIsRef && payloadLoading
                    ? h("div", { className: "payload-loading", key: "payload-loading" }, "正在还原 payload…")
                    : h("pre", { className: "json-block", key: "payload" }, prettyJson(displayPayload ?? {})),
                payloadNote ? h("div", { className: "payload-note", key: "note" }, payloadNote) : null,
                payloadMissing && !payloadLoading && !payloadNote
                    ? h("div", { className: "payload-note", key: "missing-note" }, "该记录未保留可还原的 payload。")
                    : null,
                h("div", { className: "section-title", key: "detail-title" }, "任务详情"),
                h("pre", { className: "json-block", key: "detail" }, prettyJson(userData)),
                Object.keys(metaData).length ? h("details", { className: "meta-details", key: "meta" }, [
                    h("summary", { className: "meta-summary", key: "summary" }, "内部元数据"),
                    h("pre", { className: "json-block meta-block", key: "block" }, prettyJson(metaData)),
                ]) : null,
            ]),
        ]),
    ]);
}

export function PushTaskForm({ queue, readOnly, loading, payloadText, delaySeconds, expireSeconds, options, onPayload, onDelay, onExpire, onOption, onPush }) {
    const isBusy = !!loading;
    const textOption = (name, label, type = "text") => h("label", { className: "field", key: name }, [
        h("span", { className: "field-label", key: "label" }, label),
        h("input", {
            className: "input",
            type,
            value: options[name] || "",
            disabled: readOnly || isBusy,
            onChange: (event) => onOption(name, event.target.value),
            key: "input",
        }),
    ]);
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
            h("details", { className: "advanced-options", key: "advanced" }, [
                h("summary", { className: "muted", key: "summary" }, "高级任务语义"),
                h("div", { className: "advanced-grid", key: "grid" }, [
                    textOption("action", "Action（默认 payload.action）"),
                    textOption("logicalKey", "Logical key"),
                    textOption("scheduledFor", "计划时刻", "datetime-local"),
                    textOption("notBeforeAt", "最早开始", "datetime-local"),
                    textOption("startDeadlineAt", "最晚开始", "datetime-local"),
                    textOption("dedupUntil", "去重保留至", "datetime-local"),
                    textOption("traceId", "Trace ID"),
                    textOption("parentTaskId", "Parent task ID"),
                    textOption("concurrencyKey", "Concurrency key"),
                    textOption("supersedeKey", "Supersede key"),
                    textOption("supersedeVersion", "Supersede version"),
                    h("label", { className: "field checkbox-field", key: "allowNew" }, [
                        h("input", {
                            type: "checkbox",
                            checked: !!options.allowNew,
                            disabled: readOnly || isBusy,
                            onChange: (event) => onOption("allowNew", event.target.checked),
                            key: "input",
                        }),
                        h("span", { key: "label" }, "允许同 logical key 新实例（危险）"),
                    ]),
                ]),
            ]),
            h("div", { className: "button-row", style: { marginTop: "10px" }, key: "row" }, [
                h("input", {
                    className: "input",
                    style: { width: "80px" },
                    type: "number",
                    min: "0",
                    title: "延迟秒数",
                    placeholder: "延迟(s)",
                    disabled: readOnly || isBusy,
                    value: delaySeconds,
                    onChange: (event) => onDelay(Number(event.target.value || 0)),
                    key: "delay",
                }),
                h("input", {
                    className: "input",
                    style: { width: "80px" },
                    type: "number",
                    min: "0",
                    title: "过期秒数",
                    placeholder: "过期(s)",
                    disabled: readOnly || isBusy,
                    value: expireSeconds,
                    onChange: (event) => onExpire(Number(event.target.value || 0)),
                    key: "expire",
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
