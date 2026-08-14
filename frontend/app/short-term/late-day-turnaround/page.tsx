"use client";

import { LateDayTurnaroundPanel } from "@/components/late-day-turnaround-panel";

export default function LateDayTurnaroundPage() {
  if (process.env.NEXT_PUBLIC_LATE_DAY_TURNAROUND_ENABLED !== "true") {
    return (
      <section className="rounded-[12px] border border-border bg-white p-6 shadow-card">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-accent">Research only</p>
        <h2 className="mt-2 text-xl font-semibold text-ink">尾盘转强策略暂未启用</h2>
        <p className="mt-3 text-sm leading-6 text-ink/65">启用 API 与独立物化开关后，这里只展示已持久化研究证据。</p>
      </section>
    );
  }
  return <LateDayTurnaroundPanel />;
}
