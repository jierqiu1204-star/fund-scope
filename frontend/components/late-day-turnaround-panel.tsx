"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Panel } from "@/components/ui";
import { api } from "@/lib/api";
import {
  type LateDayKind,
  type LateDayResponse,
  type LateDayUniverse,
  unavailableText
} from "@/lib/late-day-turnaround-contract";

export function LateDayTurnaroundPanel() {
  const [universe, setUniverse] = useState<LateDayUniverse>("etf");
  const [kind, setKind] = useState<LateDayKind>("formal_candidate");
  const query = useQuery({
    queryKey: ["late-day-turnaround", universe, kind],
    queryFn: async ({ signal }) =>
      (
        await api.get<LateDayResponse>(
          "/api/short-research/late-day-turnaround/candidates",
          { params: { universe, kind, limit: 100 }, signal }
        )
      ).data
  });
  const payload = query.data;

  return (
    <Panel>
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-accent">
        Research only
      </p>
      <h2 className="mt-1 text-xl font-semibold text-ink">尾盘转强策略</h2>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-ink/60">
        ETF 与个股独立筛选，只读取已物化的截止时点证据；不会改变 ETF
        综合排名、持仓、邮件提醒或交易状态。
      </p>

      <div className="mt-5 grid gap-3 sm:grid-cols-2">
        <label className="text-sm text-ink/70">
          标的池
          <select
            className="mt-1 w-full rounded-md border border-border bg-white px-3 py-2 text-ink"
            value={universe}
            onChange={(event) => setUniverse(event.target.value as LateDayUniverse)}
          >
            <option value="etf">ETF</option>
            <option value="ashare">个股</option>
          </select>
        </label>
        <label className="text-sm text-ink/70">
          证据层
          <select
            className="mt-1 w-full rounded-md border border-border bg-white px-3 py-2 text-ink"
            value={kind}
            onChange={(event) => setKind(event.target.value as LateDayKind)}
          >
            <option value="formal_candidate">正式研究候选</option>
            <option value="daily_proxy_watchlist">日线代理观察（不可交易）</option>
          </select>
        </label>
      </div>

      {query.isPending ? <p className="mt-6 text-sm text-ink/60">读取研究证据中……</p> : null}
      {query.isError ? <p className="mt-6 text-sm text-red-700">研究证据读取失败。</p> : null}
      {payload?.summary.unavailable_reason ? (
        <p className="mt-6 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950">
          {unavailableText[payload.summary.unavailable_reason] ?? payload.summary.unavailable_reason}
        </p>
      ) : null}

      {payload ? (
        <>
          <div className="mt-5 grid gap-3 sm:grid-cols-4">
            <Metric label="声明池" value={payload.summary.expected_count} />
            <Metric label="已评估" value={payload.summary.evaluated_count} />
            <Metric label="候选" value={payload.summary.qualifying_count} />
            <Metric label="覆盖率" value={`${(payload.summary.coverage_ratio * 100).toFixed(1)}%`} />
          </div>
          <p className="mt-3 break-all text-xs text-ink/55">
            决策截止：{payload.decision_at ?? "暂无"} · manifest：{payload.manifest_hash ?? "暂无"}
          </p>
          <div className="mt-5 space-y-2">
            {payload.items.map((item) => (
              <div key={`${item.asset_code}-${item.observation_kind}`} className="rounded-md border border-border p-3">
                <div className="flex items-center justify-between gap-3">
                  <p className="font-medium text-ink">{item.asset_name} <span className="font-mono text-xs text-ink/55">{item.asset_code}</span></p>
                  <span className="font-mono text-sm">{item.score?.toFixed(2) ?? "—"}</span>
                </div>
                <p className="mt-1 text-xs text-ink/55">{item.reason} · 偏离 {item.ma_deviation_pct?.toFixed(2) ?? "—"}% · 量比 {item.amount_ratio?.toFixed(2) ?? "—"}</p>
              </div>
            ))}
            {!payload.items.length && !query.isPending ? <p className="text-sm text-ink/55">当前证据层没有项目。</p> : null}
          </div>
        </>
      ) : null}
    </Panel>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md bg-paper p-3">
      <p className="text-xs text-ink/55">{label}</p>
      <p className="mt-1 font-mono text-lg">{value}</p>
    </div>
  );
}
