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
        eyebrow="资产"
        title="看清现在持有什么，赚亏多少。"
        description="这里汇总你的基金份额、市值、成本和盈亏。数据来自你手动记录或导入的交易，以及后台生成的持仓快照。"
      />

      <div className="grid gap-4 md:grid-cols-3">
        <StatPill label="当前市值" value={formatCurrency(totalValue)} />
        <StatPill label="投入成本" value={formatCurrency(totalCost)} tone="bg-white text-ink" />
        <StatPill
          label="累计盈亏"
          value={`${formatCurrency(totalPnl)} / ${totalCost ? formatPercent((totalPnl / totalCost) * 100) : "0.00%"}`}
          tone={totalPnl >= 0 ? "bg-emerald-100 text-emerald-900" : "bg-rose-100 text-rose-900"}
        />
      </div>

      {items.length === 0 ? (
        <EmptyState
          title="还没有交易记录"
          description="可以先初始化默认基金池，或者手动记录第一笔基金买入。记录之后，这里会显示持仓、资产曲线和配置占比。"
          href="/onboarding"
          label="初始化基金池"
        />
      ) : (
        <div className="grid gap-8 xl:grid-cols-[1.4fr_0.8fr]">
          <Panel>
            <SectionHeader
              eyebrow="资产曲线"
              title="组合市值变化"
              description="这张图只读取已经保存的持仓快照，所以它反映的是系统实际记录下来的资产变化。"
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
              eyebrow="配置占比"
              title="按市值看持仓权重"
              description="这张图用来快速发现是否某一只基金占比过高。"
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
                <span className="text-ink/60">份额</span>
                <span>{item.shares.toFixed(2)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink/60">成本</span>
                <span>{formatCurrency(item.cost_basis)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink/60">市值</span>
                <span>{formatCurrency(item.market_value)}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-ink/60">盈亏</span>
                <span className={item.pnl >= 0 ? "text-emerald-700" : "text-rose-700"}>
                  {formatCurrency(item.pnl)} / {formatPercent(item.pnl_pct)}
                </span>
              </div>
            </div>
            <div className="mt-6 rounded-2xl bg-paper px-4 py-3 text-xs uppercase tracking-[0.2em] text-ink/60">
              {item.is_stale ? `数据可能过期，最近日期 ${formatDate(item.as_of_date)}` : `数据截至 ${formatDate(item.as_of_date)}`}
            </div>
          </Panel>
        ))}
      </div>
    </div>
  );
}
