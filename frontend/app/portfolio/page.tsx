"use client";

import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { EmptyState, Panel, SectionHeader, StatPill } from "@/components/ui";
import { api } from "@/lib/api";
import { formatCurrency, formatDate, formatPercent } from "@/lib/format";
import type { HoldingsResponse, ValueHistoryPoint } from "@/lib/types";

const COLORS = ["#b5532d", "#1f5c4b", "#d8a657", "#7f95d1", "#cf6a87"];

export default function PortfolioPage() {
  const holdings = useQuery({
    queryKey: ["holdings"],
    queryFn: async () => (await api.get<HoldingsResponse>("/api/portfolio/holdings")).data
  });
  const history = useQuery({
    queryKey: ["portfolio-history"],
    queryFn: async () => (await api.get<ValueHistoryPoint[]>("/api/portfolio/value-history")).data
  });

  const items = holdings.data?.items ?? [];
  const totalValue = items.reduce((sum, item) => sum + item.market_value, 0);
  const totalCost = items.reduce((sum, item) => sum + item.cost_basis, 0);
  const totalPnl = totalValue - totalCost;

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="Portfolio"
        title="Every position, stripped of drama."
        description="Holdings, value history, and allocation are laid out like a monthly editorial spread so stale data and concentration show up immediately."
      />

      <div className="grid gap-4 md:grid-cols-3">
        <StatPill label="Market Value" value={formatCurrency(totalValue)} />
        <StatPill label="Cost Basis" value={formatCurrency(totalCost)} tone="bg-white text-ink" />
        <StatPill
          label="P&L"
          value={`${formatCurrency(totalPnl)} · ${totalCost ? formatPercent((totalPnl / totalCost) * 100) : "0.00%"}`}
          tone={totalPnl >= 0 ? "bg-emerald-100 text-emerald-900" : "bg-rose-100 text-rose-900"}
        />
      </div>

      {items.length === 0 ? (
        <EmptyState
          title="No transactions yet"
          description="Apply the default portfolio or log the first fund order to unlock holdings cards, value history, and allocation diagnostics."
          href="/onboarding"
          label="Open Onboarding"
        />
      ) : (
        <div className="grid gap-8 xl:grid-cols-[1.4fr_0.8fr]">
          <Panel>
            <SectionHeader
              eyebrow="Value History"
              title="Portfolio curve"
              description="The chart only reads from snapshot data, so it mirrors what the evening jobs have actually persisted."
            />
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={history.data ?? []}>
                  <defs>
                    <linearGradient id="valueFill" x1="0" x2="0" y1="0" y2="1">
                      <stop offset="5%" stopColor="#b5532d" stopOpacity={0.35} />
                      <stop offset="95%" stopColor="#b5532d" stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} />
                  <Tooltip formatter={(value: number) => formatCurrency(value)} />
                  <Area dataKey="value" stroke="#b5532d" fill="url(#valueFill)" strokeWidth={2.5} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </Panel>

          <Panel>
            <SectionHeader
              eyebrow="Allocation"
              title="Weight by market value"
              description="The donut makes concentration obvious before it turns into a regret."
            />
            <div className="h-80">
              <ResponsiveContainer width="100%" height="100%">
                <PieChart>
                  <Pie data={items} dataKey="market_value" nameKey="fund_name" innerRadius={70} outerRadius={108}>
                    {items.map((entry, index) => (
                      <Cell key={entry.fund_code} fill={COLORS[index % COLORS.length]} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(value: number) => formatCurrency(value)} />
                </PieChart>
              </ResponsiveContainer>
            </div>
          </Panel>
        </div>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {items.map((item) => (
          <Panel key={item.fund_code} className="relative overflow-hidden">
            <div className="absolute inset-x-0 top-0 h-1 rounded-full bg-gradient-to-r from-accent to-accentSoft" />
            <p className="text-xs uppercase tracking-[0.35em] text-accent">{item.fund_code}</p>
            <h3 className="mt-3 font-display text-2xl">{item.fund_name}</h3>
            <div className="mt-5 space-y-3 text-sm">
              <div className="flex justify-between">
                <span className="text-ink/60">Shares</span>
                <span>{item.shares.toFixed(2)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink/60">Cost</span>
                <span>{formatCurrency(item.cost_basis)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink/60">Market Value</span>
                <span>{formatCurrency(item.market_value)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink/60">P&L</span>
                <span className={item.pnl >= 0 ? "text-emerald-700" : "text-rose-700"}>
                  {formatCurrency(item.pnl)} · {formatPercent(item.pnl_pct)}
                </span>
              </div>
            </div>
            <div className="mt-6 rounded-2xl bg-paper px-4 py-3 text-xs uppercase tracking-[0.2em] text-ink/60">
              {item.is_stale ? `数据截至 ${formatDate(item.as_of_date)}` : `Fresh as of ${formatDate(item.as_of_date)}`}
            </div>
          </Panel>
        ))}
      </div>
    </div>
  );
}
