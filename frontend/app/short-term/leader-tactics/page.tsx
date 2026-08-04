"use client";

import { LeaderTacticsV2Panel } from "@/components/leader-tactics-v2-panel";

export default function LeaderTacticsPage() {
  if (process.env.NEXT_PUBLIC_ETF_LEADER_TACTICS_V2_ENABLED !== "true") {
    return (
      <section className="rounded-[12px] border border-border bg-white p-6 shadow-card">
        <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-accent">
          Research only
        </p>
        <h2 className="mt-2 text-xl font-semibold text-ink">
          龙头战术 V2 暂未启用
        </h2>
        <p className="mt-3 max-w-2xl text-sm leading-6 text-ink/65">
          生产采集、物化、API
          与界面开关默认关闭。完成真实数据验收并明确授权后，才会分阶段开放。
        </p>
      </section>
    );
  }

  return (
    <div className="space-y-6">
      <LeaderTacticsV2Panel />
    </div>
  );
}
