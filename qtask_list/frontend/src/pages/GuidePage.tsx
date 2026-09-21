import { useState } from "react";
import { GUIDE_OPS_ROWS, GUIDE_TEXT } from "../guideText";
import { StateBadge } from "../components/ui";

const TOC = [
  ["quick", "30 秒总览"],
  ["relation", "任务和队列的关系"],
  ["states", "任务的一生（状态）"],
  ["numbers", "关键数字怎么读"],
  ["identity", "去重：同一件事只干一次"],
  ["workers", "Worker 与心跳"],
  ["ops", "日常操作怎么做"],
  ["pages", "后台各页是干嘛的"],
] as const;

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section className="guide-section" id={id}>
      <h2>{title}</h2>
      {children}
    </section>
  );
}

export function GuidePage() {
  const [active, setActive] = useState<string>("quick");
  return (
    <div className="page">
      <h1 className="page-title">教程</h1>
      <p className="page-desc">说人话的概念手册 · 给第一次用的人看</p>

      <div className="toc-chips">
        {TOC.map(([id, label]) => (
          <a key={id} href={`#${id}`} style={active === id ? { borderColor: "var(--c-primary)", color: "var(--c-primary)" } : undefined} onClick={() => setActive(id)}>
            {label}
          </a>
        ))}
      </div>

      <Section id="quick" title="30 秒总览">
        <p>{GUIDE_TEXT.overview}</p>
        <div className="example-box flow">
          <span className="badge c-primary">你的程序投递</span>
          <span className="arrow">→</span>
          <span className="badge c-primary">队列</span>
          <span className="arrow">→</span>
          <span className="badge c-primary">Worker 执行</span>
          <span className="arrow">→</span>
          <span className="badge c-success">完成</span>
          <span className="arrow">／</span>
          <span className="badge c-warning">重试（越等越久）</span>
          <span className="arrow">→</span>
          <span className="badge c-danger">DLQ 死信（等管理员）</span>
        </div>
      </Section>

      <Section id="relation" title="任务和队列的关系">
        <p>{GUIDE_TEXT.queueTask}</p>
        <div className="example-box">
          <code>stockev:quote:5min</code> 是一个队列；"抓取 2026-09-21 10:00 这根 AAPL 行情"就是里面的一条任务，
          用 logical_key <code>quote:AAPL:20260921T1000</code> 标记身份。
        </div>
      </Section>

      <Section id="states" title="任务的一生（状态）">
        <p>{GUIDE_TEXT.states}</p>
        <div className="flow">
          <StateBadge state="ready" label="ready 排队中" />
          <span className="arrow">→</span>
          <StateBadge state="processing" label="processing 执行中" />
          <span className="arrow">→</span>
          <StateBadge state="completed" label="completed 完成" />
        </div>
        <div className="flow">
          <span className="faint">失败时：</span>
          <StateBadge state="processing" label="processing" />
          <span className="arrow">→</span>
          <StateBadge state="retry_wait" label="retry_wait 越等越久" />
          <span className="arrow">→</span>
          <StateBadge state="ready" label="再次执行" />
          <span className="arrow">→</span>
          <span className="faint">重试耗尽</span>
          <span className="arrow">→</span>
          <StateBadge state="dlq" label="dlq 死信" />
        </div>
        <div className="flow">
          <span className="faint">其他终态：</span>
          <StateBadge state="failed" label="failed 失败" />
          <StateBadge state="skipped" label="skipped 跳过" />
          <StateBadge state="cancelled" label="cancelled 取消" />
          <StateBadge state="deadline_missed" label="deadline_missed 过期" />
        </div>
      </Section>

      <Section id="numbers" title="关键数字怎么读">
        <p>{GUIDE_TEXT.numbers}</p>
      </Section>

      <Section id="identity" title="去重：同一件事只干一次">
        <p>{GUIDE_TEXT.identity}</p>
      </Section>

      <Section id="workers" title="Worker 与心跳">
        <p>{GUIDE_TEXT.workers}</p>
      </Section>

      <Section id="ops" title="日常操作怎么做">
        <table className="ops-table">
          <thead>
            <tr>
              <th>场景</th>
              <th>去哪</th>
              <th>干什么</th>
            </tr>
          </thead>
          <tbody>
            {GUIDE_OPS_ROWS.map(([a, b, c]) => (
              <tr key={a}>
                <td>{a}</td>
                <td>{b}</td>
                <td>{c}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <Section id="pages" title="后台各页是干嘛的">
        <p>{GUIDE_TEXT.pages}</p>
      </Section>
    </div>
  );
}
