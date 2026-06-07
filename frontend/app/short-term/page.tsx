"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

import { Panel, SectionHeader, StatPill } from "@/components/ui";
import { api } from "@/lib/api";
import { formatDate, formatPercent } from "@/lib/format";
import type {
  ShortResearchAsset,
  ShortResearchAssetDetail,
  ShortResearchAssetList,
  ShortResearchSignalRun,
  ShortResearchStatus
} from "@/lib/types";

type AssetTypeFilter = "all" | "fund" | "etf";
type SortKey = "score" | "return_5d" | "return_20d" | "drawdown_low" | "liquidity" | "risk_low";

const assetTypeOptions: Array<{ key: AssetTypeFilter; label: string }> = [
  { key: "all", label: "全部" },
  { key: "fund", label: "基金" },
  { key: "etf", label: "ETF" }
];

const sortOptions: Array<{ key: SortKey; label: string }> = [
  { key: "score", label: "综合排序" },
  { key: "return_5d", label: "近 5 日强" },
  { key: "return_20d", label: "近 20 日强" },
  { key: "drawdown_low", label: "回撤较小" },
  { key: "liquidity", label: "ETF 活跃" },
  { key: "risk_low", label: "风险较低" }
];

const fallbackThemes = [
  "科技",
  "AI",
  "芯片",
  "半导体",
  "新能源",
  "光伏",
  "电池",
  "医药",
  "消费",
  "金融",
  "宽基",
  "港股",
  "跨境",
  "债券",
  "红利"
];

function errorText(error: unknown) {
  if (typeof error === "object" && error !== null && "response" in error) {
    const response = (error as { response?: { data?: { detail?: string } } }).response;
    if (response?.data?.detail) {
      return response.data.detail;
    }
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "操作失败";
}

function numericMetric(metrics: Record<string, unknown>, key: string) {
  const value = metrics[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function percentMetric(metrics: Record<string, unknown>, key: string) {
  const value = numericMetric(metrics, key);
  return value === null ? "暂无" : formatPercent(value * 100);
}

function conclusionTone(conclusion: string) {
  if (conclusion === "短线观察") {
    return "bg-emerald-100 text-emerald-800";
  }
  if (conclusion === "高位观察") {
    return "bg-rose-100 text-rose-800";
  }
  if (conclusion === "谨慎观察") {
    return "bg-amber-100 text-amber-900";
  }
  if (conclusion === "数据不足") {
    return "bg-stone-200 text-stone-700";
  }
  return "bg-slate-200 text-slate-800";
}

function assetTypeLabel(assetType: string) {
  return assetType === "etf" ? "ETF" : "基金";
}

function rationaleText(asset: ShortResearchAsset, key: string, fallback: string) {
  const value = asset.rationale[key];
  return typeof value === "string" ? value : fallback;
}

function labelMeaning(label: string) {
  switch (label) {
    case "短线观察":
      return "趋势和风险条件相对更好，适合放入观察清单继续看图。";
    case "高位观察":
      return "分数不低，但追高、连续大涨或波动风险也被触发，重点防止情绪过热。";
    case "谨慎观察":
      return "有一些正面信号，但优势不够突出，需要更多数据确认。";
    case "不适合短线":
      return "近期趋势、风险或流动性条件偏弱，不适合作为短线候选。";
    case "数据不足":
      return "公开历史太短或最新数据滞后，系统不做有效判断。";
    default:
      return "仅代表研究观察标签，不代表未来收益。";
  }
}

function chartPoints(detail: ShortResearchAssetDetail | undefined) {
  return (
    detail?.chart.map((point) => ({
      date: point.date,
      label: point.date.slice(5).replace("-", "/"),
      value: point.value,
      drawdown: point.drawdown * 100,
      turnover: point.turnover ? point.turnover / 100_000_000 : null
    })) ?? []
  );
}

function returnWindowChart(asset: ShortResearchAsset | undefined) {
  if (!asset) {
    return [];
  }
  return [
    { label: "近 5 日", value: numericMetric(asset.metrics, "return_5d") },
    { label: "近 10 日", value: numericMetric(asset.metrics, "return_10d") },
    { label: "近 20 日", value: numericMetric(asset.metrics, "return_20d") },
    { label: "近 60 日", value: numericMetric(asset.metrics, "return_60d") }
  ].map((item) => ({ ...item, percent: item.value === null ? null : item.value * 100 }));
}

function safeSummary(result: Record<string, unknown> | null) {
  if (!result) {
    return null;
  }
  return JSON.stringify(result, null, 2);
}

export default function ShortTermPage() {
  const queryClient = useQueryClient();
  const [assetType, setAssetType] = useState<AssetTypeFilter>("all");
  const [theme, setTheme] = useState("all");
  const [sort, setSort] = useState<SortKey>("score");
  const [keyword, setKeyword] = useState("");
  const [selected, setSelected] = useState<{ asset_type: "fund" | "etf"; code: string } | null>(null);
  const [lastResult, setLastResult] = useState<Record<string, unknown> | null>(null);

  const status = useQuery({
    queryKey: ["short-research", "status"],
    queryFn: async () => (await api.get<ShortResearchStatus>("/api/short-research/status")).data
  });

  const latestSignals = useQuery({
    queryKey: ["short-research", "signals", "latest"],
    queryFn: async () => (await api.get<ShortResearchSignalRun | null>("/api/short-research/signals/latest")).data
  });

  const assets = useQuery({
    queryKey: ["short-research", "assets", assetType, theme, sort, keyword],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (assetType !== "all") {
        params.set("asset_type", assetType);
      }
      if (theme !== "all") {
        params.set("theme", theme);
      }
      if (keyword.trim()) {
        params.set("q", keyword.trim());
      }
      params.set("sort", sort);
      return (await api.get<ShortResearchAssetList>(`/api/short-research/assets?${params.toString()}`)).data;
    }
  });

  const selectedDetail = useQuery({
    queryKey: ["short-research", "detail", selected?.asset_type, selected?.code],
    enabled: selected !== null,
    queryFn: async () =>
      (
        await api.get<ShortResearchAssetDetail>(
          `/api/short-research/assets/${selected?.asset_type}/${selected?.code}`
        )
      ).data
  });

  useEffect(() => {
    const items = assets.data?.items ?? [];
    const first = items[0];
    if (!first) {
      setSelected(null);
      return;
    }
    if (!selected || !items.some((item) => item.asset_type === selected.asset_type && item.code === selected.code)) {
      setSelected({ asset_type: first.asset_type, code: first.code });
    }
  }, [assets.data, selected]);

  const themes = useMemo(() => {
    const seen = new Set<string>(fallbackThemes);
    for (const item of assets.data?.items ?? []) {
      item.theme_tags.forEach((tag) => seen.add(tag));
    }
    return ["all", ...Array.from(seen).sort((left, right) => left.localeCompare(right, "zh-CN"))];
  }, [assets.data]);

  const syncData = useMutation({
    mutationFn: async () =>
      (
        await api.post<Record<string, unknown>>("/api/short-research/data/sync", {
          days: 120,
          asset_type: assetType === "all" ? null : assetType
        })
      ).data,
    onSuccess: async (result) => {
      setLastResult(result);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["short-research"] }),
        queryClient.invalidateQueries({ queryKey: ["admin", "data-status"] })
      ]);
    }
  });

  const runSignals = useMutation({
    mutationFn: async () =>
      (
        await api.post<ShortResearchSignalRun>("/api/short-research/signals/run", {
          asset_type: assetType === "all" ? null : assetType,
          theme: theme === "all" ? null : theme
        })
      ).data,
    onSuccess: async (result) => {
      setLastResult({
        as_of_date: result.as_of_date,
        item_count: result.summary.item_count,
        conclusion_counts: result.summary.conclusion_counts
      });
      await queryClient.invalidateQueries({ queryKey: ["short-research"] });
    }
  });

  const selectedAsset = selectedDetail.data?.asset;
  const detailPoints = chartPoints(selectedDetail.data);
  const windowPoints = returnWindowChart(selectedAsset);
  const statusData = status.data;
  const dataIssues = statusData?.data_health.filter((item) => item.status !== "success" || item.is_stale).slice(0, 6) ?? [];

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="短线研究"
        title="基金和 ETF，一页看清近期强弱"
        description="这里只看公开基金净值和 ETF 日线，适合一两周到两三个月的观察周期。系统给排序、图表和风险解释，不给确定性结论。"
        action={
          <div className="flex flex-wrap gap-3">
            <button
              className="rounded-full border border-ink/10 bg-white px-5 py-3 text-sm font-semibold text-ink transition hover:border-accent disabled:opacity-60"
              disabled={syncData.isPending}
              onClick={() => syncData.mutate()}
            >
              {syncData.isPending ? "正在准备数据..." : "拉取近 120 天数据"}
            </button>
            <button
              className="rounded-full bg-ink px-5 py-3 text-sm font-semibold text-white transition hover:bg-pine disabled:opacity-60"
              disabled={runSignals.isPending}
              onClick={() => runSignals.mutate()}
            >
              {runSignals.isPending ? "正在生成排序..." : "生成短线排序"}
            </button>
          </div>
        }
      />

      <div className="grid gap-4 md:grid-cols-3 xl:grid-cols-6">
        <StatPill label="研究池" value={`${statusData?.asset_count ?? 0} 只`} tone="bg-white text-ink" />
        <StatPill label="基金 / ETF" value={`${statusData?.fund_count ?? 0} / ${statusData?.etf_count ?? 0}`} />
        <StatPill label="已有数据" value={`${statusData?.priced_asset_count ?? 0} 只`} tone="bg-accentSoft text-ink" />
        <StatPill label="最新数据" value={formatDate(statusData?.latest_data_date)} tone="bg-white text-ink" />
        <StatPill label="短线观察" value={`${statusData?.observable_count ?? 0} 只`} tone="bg-emerald-100 text-emerald-800" />
        <StatPill label="高位观察" value={`${statusData?.high_risk_count ?? 0} 只`} tone="bg-rose-100 text-rose-800" />
      </div>

      {(syncData.isError || runSignals.isError || status.isError) && (
        <p className="rounded-[18px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {errorText(syncData.error ?? runSignals.error ?? status.error)}
        </p>
      )}

      <Panel className="rounded-[24px]">
        <div className="grid gap-4 lg:grid-cols-[1.5fr_0.9fr]">
          <div>
            <p className="text-lg font-semibold text-ink">标签怎么理解</p>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              {["短线观察", "高位观察", "谨慎观察", "不适合短线", "数据不足"].map((label) => (
                <div key={label} className="rounded-[18px] border border-ink/10 bg-white px-4 py-3">
                  <span className={`inline-flex rounded-full px-3 py-1 text-xs font-semibold ${conclusionTone(label)}`}>
                    {label}
                  </span>
                  <p className="mt-2 text-sm leading-6 text-ink/65">{labelMeaning(label)}</p>
                </div>
              ))}
            </div>
          </div>
          <div className="rounded-[20px] bg-paper p-5">
            <p className="text-lg font-semibold text-ink">数据状态</p>
            <p className="mt-2 text-sm leading-6 text-ink/65">
              最近一次排序日期：{formatDate(latestSignals.data?.as_of_date ?? statusData?.signal_date)}。
              数据异常 {statusData?.data_issue_count ?? 0} 只，主要是缺少历史或最新数据滞后。
            </p>
            {lastResult ? (
              <pre className="mt-4 max-h-40 overflow-auto rounded-[16px] bg-white p-3 text-xs leading-5 text-ink/65">
                {safeSummary(lastResult)}
              </pre>
            ) : null}
          </div>
        </div>
      </Panel>

      <div className="grid gap-6 xl:grid-cols-[0.95fr_1.05fr]">
        <Panel className="rounded-[24px]">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="flex flex-wrap gap-2">
              {assetTypeOptions.map((option) => (
                <button
                  key={option.key}
                  className={`rounded-full px-4 py-2 text-sm font-semibold transition ${
                    assetType === option.key ? "bg-ink text-white" : "border border-ink/10 bg-white text-ink hover:border-accent"
                  }`}
                  onClick={() => setAssetType(option.key)}
                >
                  {option.label}
                </button>
              ))}
            </div>
            <input
              className="rounded-full border border-ink/10 bg-white px-4 py-2 text-sm outline-none transition focus:border-accent"
              placeholder="搜基金名或代码"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
            />
            <select
              className="rounded-full border border-ink/10 bg-white px-4 py-2 text-sm outline-none transition focus:border-accent"
              value={theme}
              onChange={(event) => setTheme(event.target.value)}
            >
              {themes.map((item) => (
                <option key={item} value={item}>
                  {item === "all" ? "全部方向" : item}
                </option>
              ))}
            </select>
            <select
              className="rounded-full border border-ink/10 bg-white px-4 py-2 text-sm outline-none transition focus:border-accent"
              value={sort}
              onChange={(event) => setSort(event.target.value as SortKey)}
            >
              {sortOptions.map((option) => (
                <option key={option.key} value={option.key}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>

          <div className="mt-5 space-y-3">
            {assets.isLoading ? (
              <div className="rounded-[20px] border border-dashed border-ink/20 p-6 text-sm text-ink/55">
                正在读取短线研究池...
              </div>
            ) : null}
            {(assets.data?.items ?? []).slice(0, 30).map((item) => {
              const isSelected = selected?.asset_type === item.asset_type && selected.code === item.code;
              return (
                <button
                  key={`${item.asset_type}-${item.code}`}
                  className={`w-full rounded-[20px] border p-4 text-left transition ${
                    isSelected ? "border-ink bg-ink text-white" : "border-ink/10 bg-white text-ink hover:border-accent"
                  }`}
                  onClick={() => setSelected({ asset_type: item.asset_type, code: item.code })}
                >
                  <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                    <div>
                      <p className={`text-xs font-semibold ${isSelected ? "text-white/60" : "text-accent"}`}>
                        #{item.rank ?? "-"} · {assetTypeLabel(item.asset_type)} · {item.theme_tags.slice(0, 3).join(" / ")}
                      </p>
                      <h3 className="mt-2 text-xl font-semibold">
                        {item.name}
                        <span className={`ml-2 text-sm font-normal ${isSelected ? "text-white/45" : "text-ink/45"}`}>
                          {item.code}
                        </span>
                      </h3>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white text-ink" : "bg-ink text-white"}`}>
                        {item.total_score.toFixed(1)} 分
                      </span>
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white/15 text-white" : conclusionTone(item.conclusion)}`}>
                        {item.conclusion}
                      </span>
                    </div>
                  </div>
                  <div className={`mt-4 grid gap-2 text-sm md:grid-cols-5 ${isSelected ? "text-white/70" : "text-ink/60"}`}>
                    <span>近 5 日：{percentMetric(item.metrics, "return_5d")}</span>
                    <span>近 20 日：{percentMetric(item.metrics, "return_20d")}</span>
                    <span>近 60 日：{percentMetric(item.metrics, "return_60d")}</span>
                    <span>60 日回撤：{percentMetric(item.metrics, "max_drawdown_60d")}</span>
                    <span>样本：{item.usable_days} 天</span>
                  </div>
                  <p className={`mt-3 line-clamp-2 text-sm leading-6 ${isSelected ? "text-white/65" : "text-ink/60"}`}>
                    {rationaleText(item, "key_reason", "按近期趋势、风险和数据质量生成。")}
                  </p>
                </button>
              );
            })}
            {!assets.isLoading && (assets.data?.items ?? []).length === 0 ? (
              <div className="rounded-[20px] border border-dashed border-ink/20 p-6 text-sm leading-6 text-ink/55">
                当前筛选条件下没有结果。可以换一个方向，或先点击“拉取近 120 天数据”。
              </div>
            ) : null}
          </div>
        </Panel>

        <div className="space-y-6">
          <Panel className="rounded-[24px]">
            {selectedAsset ? (
              <>
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div>
                    <p className="text-sm text-ink/50">
                      {assetTypeLabel(selectedAsset.asset_type)} · {selectedAsset.trading_rule_label}
                    </p>
                    <h2 className="mt-2 text-3xl font-semibold text-ink">
                      {selectedAsset.name}
                      <span className="ml-2 text-lg font-normal text-ink/45">{selectedAsset.code}</span>
                    </h2>
                    <p className="mt-2 text-sm leading-6 text-ink/65">{selectedAsset.investment_direction}</p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <span className="rounded-full bg-ink px-4 py-2 text-sm font-semibold text-white">
                      {selectedAsset.total_score.toFixed(1)} 分
                    </span>
                    <span className={`rounded-full px-4 py-2 text-sm font-semibold ${conclusionTone(selectedAsset.conclusion)}`}>
                      {selectedAsset.conclusion}
                    </span>
                  </div>
                </div>

                <div className="mt-5 grid gap-3 md:grid-cols-4">
                  <StatPill label="最新日期" value={formatDate(selectedAsset.latest_date)} tone="bg-white text-ink" />
                  <StatPill
                    label={selectedAsset.asset_type === "etf" ? "最新收盘" : "最新净值"}
                    value={selectedAsset.latest_value === null ? "暂无" : selectedAsset.latest_value.toFixed(4)}
                  />
                  <StatPill label="可用样本" value={`${selectedAsset.usable_days} 天`} tone="bg-accentSoft text-ink" />
                  <StatPill label="60 日回撤" value={percentMetric(selectedAsset.metrics, "max_drawdown_60d")} tone="bg-white text-ink" />
                </div>

                <div className="mt-6 grid gap-5 xl:grid-cols-2">
                  <div className="rounded-[20px] bg-paper p-4">
                    <p className="font-semibold text-ink">走势</p>
                    <div className="mt-4 h-72">
                      {detailPoints.length ? (
                        <ResponsiveContainer width="100%" height="100%">
                          <AreaChart data={detailPoints}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#eadfd2" />
                            <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={28} />
                            <YAxis tickLine={false} axisLine={false} width={56} domain={["dataMin", "dataMax"]} />
                            <Tooltip formatter={(value) => Number(value).toFixed(4)} />
                            <Area type="monotone" dataKey="value" name="价格/净值" stroke="#1f5c4b" fill="#dce9df" />
                          </AreaChart>
                        </ResponsiveContainer>
                      ) : (
                        <div className="flex h-full items-center justify-center rounded-[18px] bg-white text-sm text-ink/50">
                          暂无走势图。先准备数据后再查看。
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="rounded-[20px] bg-paper p-4">
                    <p className="font-semibold text-ink">从高点回落</p>
                    <div className="mt-4 h-72">
                      {detailPoints.length ? (
                        <ResponsiveContainer width="100%" height="100%">
                          <AreaChart data={detailPoints}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#eadfd2" />
                            <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={28} />
                            <YAxis tickFormatter={(value) => `${Number(value).toFixed(0)}%`} tickLine={false} axisLine={false} width={56} />
                            <Tooltip formatter={(value) => `${Number(value).toFixed(2)}%`} />
                            <Area type="monotone" dataKey="drawdown" name="回落幅度" stroke="#b5532d" fill="#f1d8ca" />
                          </AreaChart>
                        </ResponsiveContainer>
                      ) : (
                        <div className="flex h-full items-center justify-center rounded-[18px] bg-white text-sm text-ink/50">
                          暂无回撤图。
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="rounded-[20px] bg-paper p-4">
                    <p className="font-semibold text-ink">近期涨跌窗口</p>
                    <div className="mt-4 h-64">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={windowPoints}>
                          <CartesianGrid stroke="#eadfd2" vertical={false} />
                          <XAxis dataKey="label" tickLine={false} axisLine={false} />
                          <YAxis tickFormatter={(value) => `${Number(value).toFixed(0)}%`} tickLine={false} axisLine={false} width={56} />
                          <Tooltip formatter={(value) => (value === null ? "暂无" : `${Number(value).toFixed(2)}%`)} />
                          <Bar dataKey="percent" name="涨跌幅" fill="#1f5c4b" radius={[8, 8, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>

                  <div className="rounded-[20px] bg-paper p-4">
                    <p className="font-semibold text-ink">{selectedAsset.asset_type === "etf" ? "ETF 成交活跃度" : "样本和风险说明"}</p>
                    <div className="mt-4 h-64">
                      {selectedAsset.asset_type === "etf" && detailPoints.some((point) => point.turnover !== null) ? (
                        <ResponsiveContainer width="100%" height="100%">
                          <BarChart data={detailPoints.filter((point) => point.turnover !== null).slice(-60)}>
                            <CartesianGrid stroke="#eadfd2" vertical={false} />
                            <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={24} />
                            <YAxis tickFormatter={(value) => `${Number(value).toFixed(0)}亿`} tickLine={false} axisLine={false} width={56} />
                            <Tooltip formatter={(value) => `${Number(value).toFixed(2)} 亿元`} />
                            <Bar dataKey="turnover" name="成交额" fill="#e3b873" radius={[8, 8, 0, 0]} />
                          </BarChart>
                        </ResponsiveContainer>
                      ) : (
                        <div className="flex h-full flex-col justify-center rounded-[18px] bg-white p-5 text-sm leading-7 text-ink/60">
                          <p>{selectedAsset.sample_level}</p>
                          <p className="mt-2">
                            风险标签：{selectedAsset.risk_flags.length ? selectedAsset.risk_flags.join("、") : "暂未触发主要风险标签"}
                          </p>
                          <p className="mt-2">数据来源：{selectedAsset.source_note}</p>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <div className="rounded-[20px] border border-dashed border-ink/20 p-8 text-sm leading-6 text-ink/55">
                左侧选择一只基金或 ETF 后，这里会显示走势、回撤、近期涨跌和解释。
              </div>
            )}
          </Panel>

          {selectedAsset ? (
            <Panel className="rounded-[24px]">
              <p className="text-lg font-semibold text-ink">分析说明</p>
              <div className="mt-4 grid gap-4 md:grid-cols-2">
                {Object.entries(selectedDetail.data?.explanation_sections ?? {}).map(([title, text]) => (
                  <div key={title} className="rounded-[18px] border border-ink/10 bg-white p-4">
                    <p className="font-semibold text-ink">{title}</p>
                    <p className="mt-2 text-sm leading-7 text-ink/65">{text}</p>
                  </div>
                ))}
                <div className="rounded-[18px] border border-ink/10 bg-white p-4">
                  <p className="font-semibold text-ink">标签原因</p>
                  <p className="mt-2 text-sm leading-7 text-ink/65">
                    {rationaleText(selectedAsset, "label_meaning", labelMeaning(selectedAsset.conclusion))}
                  </p>
                </div>
                <div className="rounded-[18px] border border-ink/10 bg-white p-4">
                  <p className="font-semibold text-ink">风险标签</p>
                  <p className="mt-2 text-sm leading-7 text-ink/65">
                    {selectedAsset.risk_flags.length ? selectedAsset.risk_flags.join("、") : "暂未触发主要风险标签。"}
                  </p>
                </div>
              </div>
            </Panel>
          ) : null}
        </div>
      </div>

      {dataIssues.length ? (
        <Panel className="rounded-[24px]">
          <p className="text-lg font-semibold text-ink">需要留意的数据问题</p>
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {dataIssues.map((item) => (
              <div key={`${item.asset_type}-${item.code}`} className="rounded-[18px] border border-ink/10 bg-white p-4 text-sm leading-6">
                <p className="font-semibold text-ink">
                  {item.name} <span className="text-ink/45">{item.code}</span>
                </p>
                <p className="mt-1 text-ink/60">
                  {assetTypeLabel(item.asset_type)} · {item.status === "missing" ? "缺少数据" : "数据滞后"} · 最新：
                  {formatDate(item.latest_date)}
                </p>
              </div>
            ))}
          </div>
        </Panel>
      ) : null}

      <p className="rounded-[18px] bg-white/70 px-5 py-4 text-sm leading-7 text-ink/60">
        说明：短线研究只使用公开基金净值和 ETF 日线数据。基金净值通常不是盘中实时数据，ETF 也可能受数据源延迟影响。
        页面里的排序和标签用于研究观察，不代表未来收益，也不会触发真实操作。
      </p>
    </div>
  );
}
