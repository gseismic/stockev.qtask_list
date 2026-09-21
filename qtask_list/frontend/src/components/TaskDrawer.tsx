import { useEffect, useState } from "react";
import { api } from "../api";
import type { TaskRow } from "../types";
import { STATE_LABELS, type StateKey } from "../types";
import { ConfirmDialog, Skeleton, StateBadge, fmtTime, shortId } from "./ui";

const TERMINAL = new Set(["completed", "failed", "skipped", "cancelled"]);

export function TaskDrawer({
  taskId,
  stateHint,
  onClose,
  onChanged,
}: {
  taskId: string;
  stateHint?: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [task, setTask] = useState<TaskRow | null>(null);
  const [hint, setHint] = useState<string | undefined>(stateHint);
  const [error, setError] = useState<string | null>(null);
  const [payload, setPayload] = useState<{ payload?: unknown; _note?: string } | null>(null);
  const [payloadLoading, setPayloadLoading] = useState(false);
  const [confirm, setConfirm] = useState<"delete" | "replay" | "requeue" | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [deadlineInput, setDeadlineInput] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const load = async () => {
    setError(null);
    try {
      const data = await api.task(taskId);
      setTask(data);
      setPayload(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    setHint(stateHint);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId]);

  const queue = String(task?._queue ?? task?.queue ?? "");
  const state = hint || String(task?._state ?? task?.state ?? task?.outcome ?? "");
  const isTerminal = TERMINAL.has(state) || state === "dlq" || state === "history";

  const loadPayload = async () => {
    setPayloadLoading(true);
    try {
      const data = await api.taskPayload(taskId, queue, "all");
      setPayload(data);
    } catch (e) {
      setPayload({ _note: e instanceof Error ? e.message : String(e) });
    } finally {
      setPayloadLoading(false);
    }
  };

  const runAction = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setActionError(null);
    try {
      await fn();
      setConfirm(null);
      setHint(undefined);
      await load();
      onChanged();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
          <span className="mono" style={{ fontWeight: 700, fontSize: 14 }}>
            {task?.action || "任务"}
          </span>
          <span className="mono faint" title={taskId}>
            {shortId(taskId)}
          </span>
          <span style={{ flex: 1 }} />
          <StateBadge state={state} label={STATE_LABELS[state as StateKey] ?? state} />
          <button className="btn sm" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </div>
        <div className="faint" style={{ marginBottom: 14 }}>
          队列 {queue || "—"} · attempt {task?.attempt ?? "—"}/{task?.max_attempts ?? "—"}
          {task?.logical_key ? ` · logical_key=${task.logical_key}` : ""}
        </div>

        {error && <ErrorDrawer onRetry={load} />}
        {!error && !task && <Skeleton lines={8} />}

        {task && (
          <>
            <Timeline task={task} state={state} />
            <section style={{ margin: "14px 0" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <h4 style={{ margin: "8px 0" }}>Payload / Result</h4>
                <span style={{ flex: 1 }} />
                {!payload && (
                  <button className="btn sm" onClick={loadPayload} disabled={payloadLoading}>
                    {payloadLoading ? "拉取中…" : "加载完整 payload"}
                  </button>
                )}
              </div>
              {!payload && (
                <div className="faint">点击按钮从队列/历史/外存还原完整 payload（可能较大，懒加载）。</div>
              )}
              {payload && (
                <>
                  {payload._note && <div className="faint" style={{ color: "var(--c-warning)" }}>{payload._note}</div>}
                  <pre className="code">{JSON.stringify(payload.payload ?? null, null, 2)}</pre>
                </>
              )}
              {task.result !== undefined && task.result !== null && (
                <>
                  <h4 style={{ margin: "8px 0" }}>Result</h4>
                  <pre className="code">{JSON.stringify(task.result, null, 2)}</pre>
                </>
              )}
              {!task.result && <div className="faint">任务尚无 result 记录。</div>}
            </section>

            {(task.replay_of || task.replayed_by) && (
              <section className="example-box">
                <div className="faint" style={{ marginBottom: 4 }}>血缘</div>
                {task.replay_of && (
                  <div>
                    由任务 <code>{shortId(String(task.replay_of))}</code> 重放而来
                  </div>
                )}
                {task.replayed_by && (
                  <div>
                    已重放为{" "}
                    {(Array.isArray(task.replayed_by) ? task.replayed_by : [task.replayed_by]).map((id, i) => (
                      <code key={i} style={{ marginRight: 6 }}>
                        {shortId(String(id))}
                      </code>
                    ))}
                  </div>
                )}
              </section>
            )}

            {state === "processing" && (
              <div className="example-box" style={{ border: "1px solid var(--warning-dim)" }}>
                任务正在执行，如需操作请先在队列页恢复/等待完成。
              </div>
            )}
            {actionError && <div className="error-banner">⚠ {actionError}</div>}

            <div style={{ display: "flex", gap: 8, marginTop: 16, flexWrap: "wrap" }}>
              {(state === "deadline_missed" || state === "expired") && (
                <input
                  type="datetime-local"
                  className="input"
                  value={deadlineInput}
                  onChange={(e) => setDeadlineInput(e.target.value)}
                  title="新的执行截止时间"
                />
              )}
              {isTerminal && (
                <button className="btn primary" onClick={() => setConfirm("replay")} disabled={busy}>
                  重放为新任务
                </button>
              )}
              {state === "dlq" && (
                <button className="btn" onClick={() => setConfirm("requeue")} disabled={busy}>
                  从 DLQ 重入队
                </button>
              )}
              {state !== "processing" && (
                <button className="btn danger" onClick={() => setConfirm("delete")} disabled={busy}>
                  删除
                </button>
              )}
            </div>
          </>
        )}

        <ConfirmDialog
          open={confirm === "replay"}
          title="重放为新任务"
          body={
            <div>
              <p>将以当前 payload 重放并生成新任务（保留血缘）。{state === "deadline_missed" && deadlineInput && `新截止时间：${deadlineInput.replace("T", " ")}`}</p>
            </div>
          }
          confirmText="重放"
          busy={busy}
          onCancel={() => setConfirm(null)}
          onConfirm={() =>
            runAction(() =>
              api.replayTask(taskId, {
                queue: queue || undefined,
                start_deadline_at:
                  state === "deadline_missed" && deadlineInput ? new Date(deadlineInput).toISOString() : undefined,
              }),
            )
          }
        />
        <ConfirmDialog
          open={confirm === "requeue"}
          title="从 DLQ 重入队"
          body={<p>任务将回到 ready 队列，重试计数会重置。</p>}
          confirmText="重入队"
          busy={busy}
          onCancel={() => setConfirm(null)}
          onConfirm={() => runAction(() => api.requeueTask(taskId, queue, "dlq"))}
        />
        <ConfirmDialog
          open={confirm === "delete"}
          title="删除任务"
          danger
          body={<p>将删除该任务的消息与历史记录，不可恢复。</p>}
          confirmText="删除"
          busy={busy}
          onCancel={() => setConfirm(null)}
          onConfirm={() => runAction(() => api.deleteTask(taskId, queue))}
        />
      </div>
    </div>
  );
}

function ErrorDrawer({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="empty">
      <div>任务详情加载失败</div>
      <button className="btn" onClick={onRetry} style={{ marginTop: 10 }}>
        重试
      </button>
    </div>
  );
}

function Timeline({ task, state }: { task: TaskRow; state: string }) {
  const events: Array<{ time: string; label: string; color: string; note?: string }> = [];
  const c = (v: string) => v;
  events.push({
    time: fmtTime(task.created_at as number),
    label: "投递",
    color: "var(--c-primary)",
    note: task.logical_key ? `logical_key=${task.logical_key}` : undefined,
  });
  if ((task.attempt ?? 0) > 0) {
    events.push({
      time: fmtTime(task.updated_at as number),
      label: `开始执行 attempt=${task.attempt}`,
      color: "var(--c-primary)",
      note: task.worker ? `worker=${task.worker}` : undefined,
    });
  }
  if (state === "retry_wait") {
    events.push({ time: task.run_at_text ? String(task.run_at_text) : fmtTime(task.run_at), label: "等待自动重试", color: "var(--c-warning)", note: task.error ? String(task.error) : undefined });
  }
  if (state === "delay") {
    events.push({ time: task.run_at_text ? String(task.run_at_text) : fmtTime(task.run_at), label: "计划延迟执行", color: "var(--c-warning)" });
  }
  const outcomeColor: Record<string, string> = {
    completed: c("var(--c-success)"),
    failed: c("var(--c-danger)"),
    dlq: c("var(--c-danger)"),
  };
  if (TERMINAL.has(state) || state === "dlq") {
    events.push({
      time: fmtTime(task.updated_at as number),
      label: STATE_LABELS[state as StateKey] ?? state,
      color: outcomeColor[state] ?? "var(--c-muted)",
      note: task.error ? String(task.error) : undefined,
    });
  }
  return (
    <section className="card" style={{ padding: 12 }}>
      <div className="faint" style={{ marginBottom: 6 }}>时间线</div>
      <ul className="timeline">
        {events.map((e, i) => (
          <li key={i}>
            <span className="t-dot" style={{ background: e.color }} />
            <span className="t-time">{e.time}</span>
            <span>
              {e.label}
              {e.note && <span className="faint"> · {e.note}</span>}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
