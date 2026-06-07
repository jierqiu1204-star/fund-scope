"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import { Panel, SectionHeader } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate, percentileTone } from "@/lib/format";
import type { CurrentValuation } from "@/lib/types";

export default function ValuationPage() {
  const valuations = useQuery({
    queryKey: ["valuation-current"],
    queryFn: async () => (await api.get<CurrentValuation[]>("/api/valuation/current")).data
  });

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="估值"
        title="先看贵不贵，再决定要不要投。"
        description="这里用 PE（市盈率）、PB（市净率）和历史百分位做一个快速预检。百分位越高，通常代表当前估值越热。"
      />

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {(valuations.data ?? []).map((item) => (
          <Link key={item.index_code} href={`/valuation/${item.index_code}`}>
            <Panel className="h-full transition hover:-translate-y-1 hover:border-accent">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs uppercase tracking-[0.35em] text-accent">{item.index_code}</p>
                  <h3 className="mt-3 font-display text-3xl">{item.pe.toFixed(2)}</h3>
                  <p className="mt-1 text-sm text-ink/60">PE（市盈率）/ PB（市净率）{item.pb.toFixed(2)}</p>
                </div>
                <span className={`rounded-full px-3 py-2 text-xs font-semibold ${percentileTone(item.pe_percentile)}`}>
                  PE 百分位 {item.pe_percentile ?? "-"}%
                </span>
              </div>
              <div className="mt-8 space-y-3">
                <div>
                  <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-[0.25em] text-ink/55">
                    <span>PE（市盈率）百分位</span>
                    <span>{item.pe_percentile ?? "-"}%</span>
                  </div>
                  <div className="h-2 rounded-full bg-paper">
                    <div
                      className="h-2 rounded-full bg-gradient-to-r from-pine via-accentSoft to-accent"
                      style={{ width: `${item.pe_percentile ?? 0}%` }}
                    />
                  </div>
                </div>
                <div>
                  <div className="mb-2 flex items-center justify-between text-xs uppercase tracking-[0.25em] text-ink/55">
                    <span>PB（市净率）百分位</span>
                    <span>{item.pb_percentile ?? "-"}%</span>
                  </div>
                  <div className="h-2 rounded-full bg-paper">
                    <div
                      className="h-2 rounded-full bg-gradient-to-r from-pine via-accentSoft to-accent"
                      style={{ width: `${item.pb_percentile ?? 0}%` }}
                    />
                  </div>
                </div>
              </div>
              <p className="mt-8 text-xs uppercase tracking-[0.25em] text-ink/45">
                数据日期：{formatDate(item.as_of_date)}
              </p>
            </Panel>
          </Link>
        ))}
      </div>
    </div>
  );
}
