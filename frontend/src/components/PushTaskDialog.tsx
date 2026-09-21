import { useState } from "react";
import { api, type PushTaskBody } from "../api";

const SAMPLE = JSON.stringify({ hello: "world", ts: 0 }, null, 2);

export function PushTaskDialog({
  queue,
  open,
  onClose,
  onPushed,
}: {
  queue: string;
  open: boolean;
  onClose: () => void;
  onPushed: () => void;
}) {
  const [action, setAction] = useState("");
  const [payloadText, setPayloadText] = useState("{\n  \"hello\": \"world\"\n}");
  const [delay, setDelay] = useState("0");
  const [expire, setExpire] = useState("0");
  const [logicalKey, setLogicalKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!open) return null;

  const submit = async () => {
    let payload: Record<string, unknown>;
    try {
      payload = JSON.parse(payloadText || "{}");
    } catch {
      setError("payload 不是合法 JSON");
      return;
    }
    const body: PushTaskBody = {
      payload,
      action: action || null,
      delay_seconds: Number(delay) || 0,
      expire_seconds: Number(expire) || 0,
      logical_key: logicalKey || null,
    };
    setBusy(true);
    setError(null);
    try {
      await api.pushTask(queue, body);
      onPushed();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <div className="dialog" onClick={(e) => e.stopPropagation()}>
        <h3 style={{ marginTop: 0 }}>投递测试任务 → {queue}</h3>
        <div style={{ display: "grid", gap: 10 }}>
          <label className="faint">
            action（可选）
            <input className="input" style={{ width: "100%" }} value={action} onChange={(e) => setAction(e.target.value)} placeholder="如 fetch_quote" />
          </label>
          <label className="faint">
            payload（JSON）
            <textarea className="input mono" rows={6} style={{ width: "100%" }} value={payloadText} onChange={(e) => setPayloadText(e.target.value)} />
            <span style={{ cursor: "pointer", textDecoration: "underline" }} onClick={() => setPayloadText(SAMPLE)}>
              填充示例
            </span>
          </label>
          <div style={{ display: "flex", gap: 10 }}>
            <label className="faint" style={{ flex: 1 }}>
              延迟秒数
              <input className="input" type="number" min={0} style={{ width: "100%" }} value={delay} onChange={(e) => setDelay(e.target.value)} />
            </label>
            <label className="faint" style={{ flex: 1 }}>
              截止秒数（expire）
              <input className="input" type="number" min={0} style={{ width: "100%" }} value={expire} onChange={(e) => setExpire(e.target.value)} />
            </label>
          </div>
          <label className="faint">
            logical_key（可选，幂等去重）
            <input className="input mono" style={{ width: "100%" }} value={logicalKey} onChange={(e) => setLogicalKey(e.target.value)} placeholder="如 quote:AAPL:20260921T1000" />
          </label>
          {error && <div className="error-banner" style={{ marginBottom: 0 }}>⚠ {error}</div>}
        </div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 16 }}>
          <button className="btn" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button className="btn primary" onClick={submit} disabled={busy}>
            {busy ? "投递中…" : "投递"}
          </button>
        </div>
      </div>
    </div>
  );
}
