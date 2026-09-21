import type { QueueInfo } from "./types";

export interface Sample {
  ts: number;
  completed: number;
}

export interface QueueProgress {
  remaining: number;
  completed1h: number | null;
  ratePerMin: number | null;
  etaText: string | null;
  progressPct: number | null;
  stalled: boolean;
}

const WINDOW_MS = 60 * 60 * 1000;

function remainingOf(stats: Record<string, number>): number {
  return (stats.queue ?? 0) + (stats.processing ?? 0) + (stats.retry_wait ?? 0) + (stats.delay ?? 0);
}

export class RateSampler {
  private samples = new Map<string, Sample[]>();

  observe(queues: QueueInfo[]) {
    const now = Date.now();
    const seen = new Set<string>();
    for (const item of queues) {
      const name = String(item.name);
      seen.add(name);
      const arr = this.samples.get(name) ?? [];
      arr.push({ ts: now, completed: Number(item.completed ?? 0) });
      while (arr.length > 0 && now - arr[0].ts > WINDOW_MS + 5 * 60 * 1000) arr.shift();
      this.samples.set(name, arr);
    }
    for (const key of this.samples.keys()) {
      if (!seen.has(key)) this.samples.delete(key);
    }
  }

  progress(name: string, stats: Record<string, number>): QueueProgress {
    const arr = this.samples.get(name) ?? [];
    const remaining = remainingOf(stats);
    const empty: QueueProgress = {
      remaining,
      completed1h: null,
      ratePerMin: null,
      etaText: null,
      progressPct: null,
      stalled: false,
    };
    if (arr.length < 2) return empty;

    const last = arr[arr.length - 1];
    const hourAgo = last.ts - WINDOW_MS;
    let base: Sample | null = null;
    for (const s of arr) {
      if (s.ts <= hourAgo) base = s;
      else break;
    }
    const ref = base ?? arr[0];
    const completed1h = Math.max(0, last.completed - ref.completed);
    const spanMin = Math.max((last.ts - ref.ts) / 60000, 1 / 60);
    const ratePerMin = completed1h / spanMin;
    const stalled = remaining > 0 && ratePerMin < 0.01;
    const progressPct =
      completed1h + remaining > 0 ? (completed1h / (completed1h + remaining)) * 100 : null;

    let etaText: string | null = null;
    if (remaining > 0 && ratePerMin > 0.01) {
      const minutes = remaining / ratePerMin;
      etaText = minutes < 90 ? `约${Math.round(minutes)}min` : `约${(minutes / 60).toFixed(1)}h`;
    }
    return { remaining, completed1h, ratePerMin, etaText, progressPct, stalled };
  }
}

export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return n.toLocaleString("zh-CN");
}

export function fmtRate(rate: number | null): string {
  if (rate === null) return "—";
  if (rate >= 100) return `${Math.round(rate)}/min`;
  return `${rate.toFixed(1)}/min`;
}
