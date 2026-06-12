"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

import { Panel, SectionHeader, StatPill } from "@/components/ui";
import { api } from "@/lib/api";
import { formatCurrency, formatDate, formatPercent } from "@/lib/format";
import type {
  ShortResearchAsset,
  ShortResearchAssetDetail,
  ShortResearchAssetList,
  ShortResearchSignalRun,
  ShortResearchStatus,
  TrackedPosition,
  TrackedPositionDetail,
  TrackedPositionList
} from "@/lib/types";

const SHORT_TERM_ASSET_TYPE = "fund";

type SortKey = "score" | "return_5d" | "return_20d" | "drawdown_low" | "risk_low";
type OrderTimeBucket = "before_15" | "after_15" | "unknown";

const sortOptions: Array<{ key: SortKey; label: string }> = [
  { key: "score", label: "综合排序" },
  { key: "return_5d", label: "近 5 日强" },
  { key: "return_20d", label: "近 20 日强" },
  { key: "drawdown_low", label: "回撤较小" },
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

function advisorTone(action: string) {
  if (action === "重点观察") {
    return "bg-emerald-100 text-emerald-800";
  }
  if (action === "高位别追" || action === "退出观察") {
    return "bg-rose-100 text-rose-800";
  }
  if (action === "谨慎") {
    return "bg-amber-100 text-amber-900";
  }
  return "bg-stone-200 text-stone-700";
}

function assetTypeLabel(assetType: string) {
  return assetType === "etf" ? "非支付宝资产" : "场外基金";
}

function assetTradingNote(assetType: string, name?: string) {
  if (assetType === "etf") {
    return "当前支付宝场外基金页面默认不展示这类资产。";
  }
  if (name?.includes("ETF联接")) {
    return "支付宝可买；名字带 ETF联接，但仍是场外基金，非实时净值，按确认净值日估算。";
  }
  return "支付宝可买，非实时净值，按确认净值日估算。";
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

function trackingChartPoints(detail: TrackedPositionDetail | undefined) {
  return (
    detail?.chart.map((point) => ({
      date: point.date,
      label: point.date.slice(5).replace("-", "/"),
      pnl: point.estimated_pnl_pct,
      stop: point.trailing_stop_pnl_pct,
      isEntry: point.is_entry,
      isHigh: point.is_high,
      isCurrent: point.is_current
    })) ?? []
  );
}

function safeSummary(result: Record<string, unknown> | null) {
  if (!result) {
    return null;
  }
  return JSON.stringify(result, null, 2);
}

function todayInputValue() {
  return new Date().toISOString().slice(0, 10);
}

function defaultOrderTimeBucket(): OrderTimeBucket {
  return new Date().getHours() >= 15 ? "after_15" : "before_15";
}

function orderTimeBucketLabel(value: string) {
  if (value === "before_15") {
    return "15:00 前";
  }
  if (value === "after_15") {
    return "15:00 后";
  }
  return "未填写";
}

function optionalNumber(value: string) {
  const trimmed = value.trim();
  return trimmed ? Number(trimmed) : undefined;
}

function trackingStatusLabel(status: string) {
  switch (status) {
    case "active":
      return "追踪中";
    case "handled":
      return "已处理";
    case "closed":
      return "已卖出";
    case "stopped":
      return "已停止";
    default:
      return status;
  }
}

function alertTypeLabel(alertType: string) {
  if (alertType === "exit_watch") {
    return "退出观察提醒";
  }
  if (alertType === "risk_warning") {
    return "风险提醒";
  }
  if (alertType === "take_profit_watch") {
    return "止盈观察提醒";
  }
  if (alertType === "trailing_take_profit") {
    return "移动止盈提醒";
  }
  if (alertType === "trend_weakening") {
    return "趋势转弱提醒";
  }
  if (alertType === "hard_stop") {
    return "硬止损提醒";
  }
  return alertType;
}

function exitSignalTone(level: string) {
  if (level === "urgent") {
    return "bg-rose-100 text-rose-800";
  }
  if (level === "warning") {
    return "bg-amber-100 text-amber-900";
  }
  if (level === "watch") {
    return "bg-sky-100 text-sky-800";
  }
  return "bg-paper text-ink/60";
}

function pnlTone(value: number | null) {
  if (value === null) {
    return "text-ink/55";
  }
  if (value > 0) {
    return "text-emerald-700";
  }
  if (value < 0) {
    return "text-rose-700";
  }
  return "text-ink";
}

function pnlText(position: TrackedPosition) {
  const snapshot = position.current_snapshot;
  if (snapshot.estimated_pnl === null || snapshot.estimated_pnl_pct === null) {
    return "等待净值";
  }
  return `${formatCurrency(snapshot.estimated_pnl)} / ${snapshot.estimated_pnl_pct.toFixed(2)}%`;
}

function percentOrWaiting(value: number | null) {
  return value === null ? "等待数据" : formatPercent(value);
}

export default function ShortTermPage() {
  const queryClient = useQueryClient();
  const [theme, setTheme] = useState("all");
  const [sort, setSort] = useState<SortKey>("score");
  const [keyword, setKeyword] = useState("");
  const [selected, setSelected] = useState<{ asset_type: "fund" | "etf"; code: string } | null>(null);
  const [lastResult, setLastResult] = useState<Record<string, unknown> | null>(null);
  const [trackingOpen, setTrackingOpen] = useState(false);
  const [trackingAmount, setTrackingAmount] = useState("3000");
  const [trackingDate, setTrackingDate] = useState(todayInputValue());
  const [trackingOrderTime, setTrackingOrderTime] = useState<OrderTimeBucket>(defaultOrderTimeBucket());
  const [trackingConfirmedNavDate, setTrackingConfirmedNavDate] = useState("");
  const [trackingConfirmedNav, setTrackingConfirmedNav] = useState("");
  const [trackingConfirmedShares, setTrackingConfirmedShares] = useState("");
  const [trackingNote, setTrackingNote] = useState("");

  const status = useQuery({
    queryKey: ["short-research", "status"],
    queryFn: async () => (await api.get<ShortResearchStatus>("/api/short-research/status")).data
  });

  const latestSignals = useQuery({
    queryKey: ["short-research", "signals", "latest", SHORT_TERM_ASSET_TYPE],
    queryFn: async () =>
      (await api.get<ShortResearchSignalRun | null>(`/api/short-research/signals/latest?asset_type=${SHORT_TERM_ASSET_TYPE}`)).data
  });

  const assets = useQuery({
    queryKey: ["short-research", "assets", SHORT_TERM_ASSET_TYPE, theme, sort, keyword],
    queryFn: async () => {
      const params = new URLSearchParams();
      params.set("asset_type", SHORT_TERM_ASSET_TYPE);
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

  const trackedPositions = useQuery({
    queryKey: ["tracked-positions"],
    queryFn: async () => (await api.get<TrackedPositionList>("/api/tracked-positions")).data
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
          asset_type: SHORT_TERM_ASSET_TYPE
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
          asset_type: SHORT_TERM_ASSET_TYPE,
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

  const runAdvisor = useMutation({
    mutationFn: async () =>
      (
        await api.post<Record<string, unknown>>("/api/short-research/advisor/run", {
          asset_type: SHORT_TERM_ASSET_TYPE,
          theme: theme === "all" ? null : theme
        })
      ).data,
    onSuccess: async (result) => {
      setLastResult(result);
      await queryClient.invalidateQueries({ queryKey: ["short-research"] });
    }
  });

  const createTracking = useMutation({
    mutationFn: async () => {
      if (!selectedAsset) {
        throw new Error("请先选择一只场外基金");
      }
      return (
        await api.post<TrackedPosition>("/api/tracked-positions", {
          asset_type: selectedAsset.asset_type,
          asset_code: selectedAsset.code,
          buy_amount: Number(trackingAmount),
          buy_date: trackingDate,
          order_time_bucket: trackingOrderTime,
          confirmed_nav_date: trackingConfirmedNavDate || undefined,
          confirmed_nav: optionalNumber(trackingConfirmedNav),
          confirmed_shares: optionalNumber(trackingConfirmedShares),
          note: trackingNote || undefined
        })
      ).data;
    },
    onSuccess: async () => {
      setTrackingOpen(false);
      setTrackingAmount("3000");
      setTrackingDate(todayInputValue());
      setTrackingOrderTime(defaultOrderTimeBucket());
      setTrackingConfirmedNavDate("");
      setTrackingConfirmedNav("");
      setTrackingConfirmedShares("");
      setTrackingNote("");
      await queryClient.invalidateQueries({ queryKey: ["tracked-positions"] });
    }
  });

  const closeTracking = useMutation({
    mutationFn: async (positionId: number) =>
      (
        await api.post<TrackedPosition>(`/api/tracked-positions/${positionId}/close`, {
          status: "closed",
          note: "已在平台外手动处理"
        })
      ).data,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["tracked-positions"] });
    }
  });

  const selectedAsset = selectedDetail.data?.asset;
  const advisorReport = selectedAsset?.advisor_report ?? null;
  const detailPoints = chartPoints(selectedDetail.data);
  const windowPoints = returnWindowChart(selectedAsset);
  const statusData = status.data;
  const fundHealth = statusData?.data_health.filter((item) => item.asset_type === SHORT_TERM_ASSET_TYPE) ?? [];
  const fundDataIssues = fundHealth.filter((item) => item.status !== "success" || item.is_stale);
  const fundDates = fundHealth
    .map((item) => item.latest_date)
    .filter((item): item is string => item !== null)
    .sort();
  const fundLatestDate = fundDates.length ? fundDates[fundDates.length - 1] : null;
  const visibleAssets = assets.data?.items ?? [];
  const observableCount = visibleAssets.filter((item) => item.conclusion === "短线观察").length;
  const highRiskCount = visibleAssets.filter((item) => item.conclusion === "高位观察").length;
  const dataIssues = fundDataIssues.slice(0, 6);
  const activeTracked = (trackedPositions.data?.items ?? []).filter(
    (item) => item.status === "active" && item.asset_type === SHORT_TERM_ASSET_TYPE
  );
  const selectedTracked = activeTracked.filter(
    (item) => selectedAsset && item.asset_type === selectedAsset.asset_type && item.asset_code === selectedAsset.code
  );
  const primaryTracked = selectedTracked[0] ?? null;
  const trackedDetail = useQuery({
    queryKey: ["tracked-position", primaryTracked?.id],
    enabled: primaryTracked !== null,
    queryFn: async () => (await api.get<TrackedPositionDetail>(`/api/tracked-positions/${primaryTracked?.id}`)).data
  });
  const trackingPoints = trackingChartPoints(trackedDetail.data);
  const trackingEntry = trackingPoints.find((point) => point.isEntry);
  const trackingHigh = trackingPoints.find((point) => point.isHigh);
  const trackingCurrent = trackingPoints.find((point) => point.isCurrent);

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="短线研究"
        title="场外基金短线研究"
        description="这里只看支付宝可手动买入的场外基金，适合一两周到两三个月的观察周期。数据来自公开基金净值，不是支付宝实时收益；15:00 后下单通常按下一交易日确认净值估算。名字带“ETF联接”的仍按场外基金处理。"
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
            <button
              className="rounded-full border border-ink/10 bg-white px-5 py-3 text-sm font-semibold text-ink transition hover:border-accent disabled:opacity-60"
              disabled={runAdvisor.isPending}
              onClick={() => runAdvisor.mutate()}
            >
              {runAdvisor.isPending ? "正在生成报告..." : "生成 AI 研究报告"}
            </button>
          </div>
        }
      />

      <div className="grid gap-4 md:grid-cols-3 xl:grid-cols-6">
        <StatPill label="场外基金池" value={`${statusData?.fund_count ?? 0} 只`} tone="bg-white text-ink" />
        <StatPill label="分类口径" value="场外基金（非实时净值）" />
        <StatPill label="已有数据" value={`${fundHealth.filter((item) => item.usable_days > 0).length} 只`} tone="bg-accentSoft text-ink" />
        <StatPill label="最新净值" value={formatDate(fundLatestDate)} tone="bg-white text-ink" />
        <StatPill label="短线观察" value={`${observableCount} 只`} tone="bg-emerald-100 text-emerald-800" />
        <StatPill label="高位观察" value={`${highRiskCount} 只`} tone="bg-rose-100 text-rose-800" />
      </div>

      {(syncData.isError || runSignals.isError || runAdvisor.isError || status.isError) && (
        <p className="rounded-[18px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {errorText(syncData.error ?? runSignals.error ?? runAdvisor.error ?? status.error)}
        </p>
      )}

      <Panel className="rounded-[24px]">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">我的短线追踪</p>
            <h2 className="mt-2 text-2xl font-semibold text-ink">标注你已经在支付宝买入的场外基金</h2>
            <p className="mt-2 text-sm leading-6 text-ink/65">
              这里记录的是你在支付宝手动买入后的观察笔记。场外基金不是实时净值，系统每天检查公开数据，触发止盈观察、移动止盈、趋势转弱或明显风险时给你发邮件。
            </p>
          </div>
          <div className="rounded-[18px] bg-paper px-4 py-3 text-sm leading-6 text-ink/65">
            收件邮箱：{trackedPositions.data?.recipient_email ?? "19535838578@163.com"}
            <br />
            邮件通道：{trackedPositions.data?.email_configured ? "已配置" : "未配置授权码"}
          </div>
        </div>

        <div className="mt-5 grid gap-3 xl:grid-cols-3">
          {activeTracked.map((item) => (
            <div key={item.id} className="rounded-[20px] border border-ink/10 bg-white p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold text-accent">{trackingStatusLabel(item.status)}</p>
                  <h3 className="mt-1 text-lg font-semibold text-ink">
                    {item.asset_name}
                    <span className="ml-2 text-sm font-normal text-ink/45">{item.asset_code}</span>
                  </h3>
                </div>
                <span className={`rounded-full px-3 py-1 text-xs font-semibold ${conclusionTone(item.current_snapshot.current_label ?? "数据不足")}`}>
                  {item.current_snapshot.current_label ?? "等待排序"}
                </span>
              </div>
              <div className="mt-4 grid gap-2 text-sm text-ink/65">
                <span>
                  下单：{formatCurrency(item.buy_amount)} / {formatDate(item.buy_date)}（{orderTimeBucketLabel(item.order_time_bucket)}）
                </span>
                <span>确认净值日：{formatDate(item.confirmed_nav_date ?? item.entry_price_date)}</span>
                <span>
                  确认净值：{item.confirmed_nav?.toFixed(4) ?? item.entry_price?.toFixed(4) ?? "等待净值"}
                </span>
                <span>持有：{item.holding_days === null ? "等待数据" : `${item.holding_days} 天`}</span>
                <span>
                  {item.confirmed_shares === null ? "估算份额" : "确认份额"}：
                  {item.estimated_shares === null ? "等待净值" : item.estimated_shares.toFixed(2)}
                </span>
                <span className={pnlTone(item.current_snapshot.estimated_pnl)}>估算盈亏：{pnlText(item)}</span>
                <span>最高盈利：{percentOrWaiting(item.max_profit_pct)}</span>
                <span>高点回吐：{percentOrWaiting(item.profit_giveback_pct)}</span>
                <span>当前价：{item.current_snapshot.current_price?.toFixed(4) ?? "暂无"}</span>
              </div>
              <div className={`mt-4 rounded-[16px] p-3 text-sm leading-6 ${exitSignalTone(item.exit_signal.level)}`}>
                <p className="font-semibold">{item.exit_signal.label}</p>
                <p className="mt-1">{item.exit_signal.reason ?? "暂无卖出/减仓提醒，继续观察公开数据。"}</p>
              </div>
              {item.latest_alert ? (
                <div className="mt-4 rounded-[16px] bg-rose-50 p-3 text-sm leading-6 text-rose-800">
                  {alertTypeLabel(item.latest_alert.alert_type)}：{item.latest_alert.reasons[0] ?? item.latest_alert.trigger_label}
                </div>
              ) : (
                <p className="mt-4 rounded-[16px] bg-paper p-3 text-sm leading-6 text-ink/55">
                  暂无邮件提醒。高位观察不会单独触发邮件，必须同时有盈利保护、趋势转弱或明显风险。
                </p>
              )}
              <button
                className="mt-4 rounded-full border border-ink/10 px-4 py-2 text-sm font-semibold text-ink transition hover:border-accent hover:text-accent disabled:opacity-60"
                disabled={closeTracking.isPending}
                onClick={() => closeTracking.mutate(item.id)}
              >
                标记已卖出 / 停止提醒
              </button>
            </div>
          ))}
          {!trackedPositions.isLoading && activeTracked.length === 0 ? (
            <div className="rounded-[20px] border border-dashed border-ink/20 bg-white p-5 text-sm leading-7 text-ink/55 xl:col-span-3">
              还没有追踪记录。左侧选择一只场外基金后，点“我已买入，开始追踪”。
            </div>
          ) : null}
        </div>
      </Panel>

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
              场外基金数据异常 {fundDataIssues.length} 只，主要是缺少历史或最新净值滞后。
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
            <div className="rounded-full border border-ink/10 bg-white px-4 py-2 text-sm font-semibold text-ink">
              场外基金（非实时净值）
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
                      <p className={`mt-2 text-xs leading-5 ${isSelected ? "text-white/55" : "text-ink/50"}`}>
                        {assetTradingNote(item.asset_type, item.name)}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white text-ink" : "bg-ink text-white"}`}>
                        {item.total_score.toFixed(1)} 分
                      </span>
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white/15 text-white" : conclusionTone(item.conclusion)}`}>
                        {item.conclusion}
                      </span>
                      {item.advisor_report ? (
                        <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white/15 text-white" : advisorTone(item.advisor_report.action_label)}`}>
                          {item.advisor_report.action_label}
                        </span>
                      ) : null}
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
                当前筛选条件下没有场外基金结果。可以换一个方向，或先点击“拉取近 120 天数据”。
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
                    <p className="mt-2 rounded-[14px] bg-paper px-4 py-3 text-sm leading-6 text-ink/65">
                      {assetTradingNote(selectedAsset.asset_type, selectedAsset.name)}
                    </p>
                    <p className="mt-2 text-sm leading-6 text-ink/65">{selectedAsset.investment_direction}</p>
                    {selectedTracked.length ? (
                      <p className="mt-2 text-sm text-emerald-700">你正在追踪这只资产的 {selectedTracked.length} 笔买入。</p>
                    ) : null}
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

                <div className="mt-5 rounded-[20px] border border-ink/10 bg-white p-4">
                  <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div>
                      <p className="text-sm font-semibold text-ink">我已买入，开始追踪</p>
                      <p className="mt-1 text-sm leading-6 text-ink/60">
                        输入你手动买入的金额和下单时间。15:00 后下单会按下一条公开净值估算；支付宝已确认份额时可以手动填入。
                      </p>
                    </div>
                    <button
                      className="rounded-full bg-accent px-5 py-3 text-sm font-semibold text-white transition hover:bg-pine"
                      onClick={() => setTrackingOpen((value) => !value)}
                    >
                      {trackingOpen ? "收起" : "我已买入，开始追踪"}
                    </button>
                  </div>
                  {trackingOpen ? (
                    <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                      <label className="text-sm text-ink/65">
                        买入金额
                        <input
                          className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent"
                          inputMode="decimal"
                          value={trackingAmount}
                          onChange={(event) => setTrackingAmount(event.target.value)}
                        />
                      </label>
                      <label className="text-sm text-ink/65">
                        下单日期
                        <input
                          className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent"
                          type="date"
                          value={trackingDate}
                          onChange={(event) => setTrackingDate(event.target.value)}
                        />
                      </label>
                      <label className="text-sm text-ink/65">
                        下单时间
                        <select
                          className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent"
                          value={trackingOrderTime}
                          onChange={(event) => setTrackingOrderTime(event.target.value as OrderTimeBucket)}
                        >
                          <option value="before_15">15:00 前</option>
                          <option value="after_15">15:00 后</option>
                          <option value="unknown">不确定，按旧方式估算</option>
                        </select>
                      </label>
                      <label className="text-sm text-ink/65">
                        确认净值日（可选）
                        <input
                          className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent"
                          type="date"
                          value={trackingConfirmedNavDate}
                          onChange={(event) => setTrackingConfirmedNavDate(event.target.value)}
                        />
                      </label>
                      <label className="text-sm text-ink/65">
                        支付宝确认净值（可选）
                        <input
                          className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent"
                          inputMode="decimal"
                          placeholder="例如：7.3130"
                          value={trackingConfirmedNav}
                          onChange={(event) => setTrackingConfirmedNav(event.target.value)}
                        />
                      </label>
                      <label className="text-sm text-ink/65">
                        支付宝确认份额（可选）
                        <input
                          className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent"
                          inputMode="decimal"
                          placeholder="确认份额最准确"
                          value={trackingConfirmedShares}
                          onChange={(event) => setTrackingConfirmedShares(event.target.value)}
                        />
                      </label>
                      <label className="text-sm text-ink/65">
                        备注
                        <input
                          className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent"
                          placeholder="例如：支付宝手动买入"
                          value={trackingNote}
                          onChange={(event) => setTrackingNote(event.target.value)}
                        />
                      </label>
                      <button
                        className="self-end rounded-full bg-ink px-5 py-3 text-sm font-semibold text-white transition hover:bg-pine disabled:opacity-60"
                        disabled={createTracking.isPending || Number(trackingAmount) <= 0}
                        onClick={() => createTracking.mutate()}
                      >
                        {createTracking.isPending ? "保存中..." : "保存追踪"}
                      </button>
                    </div>
                  ) : null}
                  {createTracking.isError ? (
                    <p className="mt-3 rounded-[16px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
                      创建追踪失败：{errorText(createTracking.error)}
                    </p>
                  ) : null}
                </div>

                {primaryTracked ? (
                  <div className="mt-5 rounded-[20px] border border-ink/10 bg-white p-4">
                    <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                      <div>
                        <p className="text-sm font-semibold text-ink">追踪收益保护图</p>
                        <p className="mt-1 text-sm leading-6 text-ink/60">
                          展示这笔买入后的估算收益、历史最高盈利和移动止盈线。提醒只是卖出/减仓检查，不会替你自动交易。
                        </p>
                      </div>
                      <span className={`w-fit rounded-full px-3 py-1 text-xs font-semibold ${exitSignalTone(primaryTracked.exit_signal.level)}`}>
                        {primaryTracked.exit_signal.label}
                      </span>
                    </div>
                    <div className="mt-4 h-64">
                      {trackingPoints.length ? (
                        <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={trackingPoints}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#eadfd2" />
                            <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={28} />
                            <YAxis tickFormatter={(value) => `${Number(value).toFixed(0)}%`} tickLine={false} axisLine={false} width={56} />
                            <Tooltip formatter={(value) => (value === null ? "暂无" : `${Number(value).toFixed(2)}%`)} />
                            <Line type="monotone" dataKey="pnl" name="估算收益" stroke="#1f5c4b" strokeWidth={2} dot={false} connectNulls />
                            <Line
                              type="monotone"
                              dataKey="stop"
                              name="移动止盈线"
                              stroke="#b5532d"
                              strokeDasharray="5 5"
                              strokeWidth={2}
                              dot={false}
                              connectNulls
                            />
                          </LineChart>
                        </ResponsiveContainer>
                      ) : (
                        <div className="flex h-full items-center justify-center rounded-[18px] bg-paper text-sm text-ink/50">
                          暂无追踪曲线，等待公开净值更新。
                        </div>
                      )}
                    </div>
                    <div className="mt-3 grid gap-2 text-sm text-ink/65 md:grid-cols-3">
                      <span>确认点：{trackingEntry ? `${trackingEntry.label} / ${percentOrWaiting(trackingEntry.pnl)}` : "等待数据"}</span>
                      <span>最高点：{trackingHigh ? `${trackingHigh.label} / ${percentOrWaiting(trackingHigh.pnl)}` : "等待数据"}</span>
                      <span>当前点：{trackingCurrent ? `${trackingCurrent.label} / ${percentOrWaiting(trackingCurrent.pnl)}` : "等待数据"}</span>
                    </div>
                    <p className="mt-3 rounded-[16px] bg-paper px-4 py-3 text-sm leading-6 text-ink/65">
                      {primaryTracked.exit_signal.reason ?? "暂无卖出/减仓提醒，继续观察公开数据。"}
                    </p>
                  </div>
                ) : null}

                <div className="mt-5 grid gap-3 md:grid-cols-4">
                  <StatPill label="最新日期" value={formatDate(selectedAsset.latest_date)} tone="bg-white text-ink" />
                  <StatPill label="最新净值" value={selectedAsset.latest_value === null ? "暂无" : selectedAsset.latest_value.toFixed(4)} />
                  <StatPill label="可用样本" value={`${selectedAsset.usable_days} 天`} tone="bg-accentSoft text-ink" />
                  <StatPill label="60 日回撤" value={percentMetric(selectedAsset.metrics, "max_drawdown_60d")} tone="bg-white text-ink" />
                </div>

                <div className="mt-6 rounded-[20px] border border-ink/10 bg-white p-5">
                  <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">今日研究建议</p>
                      <h3 className="mt-2 text-xl font-semibold text-ink">
                        {advisorReport ? advisorReport.plain_summary : "还没有 AI 研究报告，先看规则解释。"}
                      </h3>
                      <p className="mt-2 text-sm leading-6 text-ink/60">
                        {advisorReport
                          ? `${advisorReport.source === "llm" ? "大模型辅助" : "规则降级"} · ${advisorReport.model_name} · ${formatDate(advisorReport.generated_at)}`
                          : "点击页面顶部“生成 AI 研究报告”后，会在这里显示多角度说明。没有报告时，排序和图表仍然正常可用。"}
                      </p>
                    </div>
                    <span className={`w-fit rounded-full px-4 py-2 text-sm font-semibold ${advisorTone(advisorReport?.action_label ?? "暂不考虑")}`}>
                      {advisorReport?.action_label ?? "暂无报告"}
                    </span>
                  </div>
                  {advisorReport ? (
                    <div className="mt-5 grid gap-4 md:grid-cols-2">
                      <div className="rounded-[18px] bg-paper p-4">
                        <p className="font-semibold text-ink">为什么</p>
                        <ul className="mt-2 space-y-2 text-sm leading-6 text-ink/65">
                          {advisorReport.opportunity.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      </div>
                      <div className="rounded-[18px] bg-paper p-4">
                        <p className="font-semibold text-ink">风险和反方</p>
                        <ul className="mt-2 space-y-2 text-sm leading-6 text-ink/65">
                          {advisorReport.risks.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                        <p className="mt-3 text-sm leading-6 text-ink/65">{advisorReport.opposing_view}</p>
                      </div>
                      <div className="rounded-[18px] bg-paper p-4">
                        <p className="font-semibold text-ink">接下来观察</p>
                        <ul className="mt-2 space-y-2 text-sm leading-6 text-ink/65">
                          {advisorReport.watch_conditions.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      </div>
                      <div className="rounded-[18px] bg-paper p-4">
                        <p className="font-semibold text-ink">持有和数据限制</p>
                        <p className="mt-2 text-sm leading-6 text-ink/65">{advisorReport.holding_note}</p>
                        <p className="mt-3 text-sm leading-6 text-ink/65">{advisorReport.data_limitations}</p>
                      </div>
                    </div>
                  ) : null}
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
                    <p className="font-semibold text-ink">样本和风险说明</p>
                    <div className="mt-4 h-64">
                      <div className="flex h-full flex-col justify-center rounded-[18px] bg-white p-5 text-sm leading-7 text-ink/60">
                        <p>{selectedAsset.sample_level}</p>
                        <p className="mt-2">
                          风险标签：{selectedAsset.risk_flags.length ? selectedAsset.risk_flags.join("、") : "暂未触发主要风险标签"}
                        </p>
                        <p className="mt-2">数据来源：{selectedAsset.source_note}</p>
                        <p className="mt-2">交易口径：支付宝场外基金，按确认净值日估算，不是盘中实时价格。</p>
                      </div>
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <div className="rounded-[20px] border border-dashed border-ink/20 p-8 text-sm leading-6 text-ink/55">
                左侧选择一只场外基金后，这里会显示走势、回撤、近期涨跌和解释。
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
        说明：短线研究只使用公开基金净值。场外基金净值通常不是盘中实时数据；名字里有“ETF联接”的仍按场外基金净值确认。
        页面里的排序、标签和 AI 说明都用于研究观察，不代表未来收益，也不会触发真实操作；真实买卖仍需要你在支付宝手动确认。
      </p>
    </div>
  );
}
