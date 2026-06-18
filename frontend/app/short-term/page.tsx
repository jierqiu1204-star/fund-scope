"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { CSSProperties, ReactNode } from "react";
import { useEffect, useMemo, useRef, useState } from "react";
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

import { Panel, StatPill } from "@/components/ui";
import { api } from "@/lib/api";
import { formatCurrency, formatDate, formatPercent } from "@/lib/format";
import type {
  ShortResearchAsset,
  ShortResearchAssetDetail,
  ShortResearchAssetList,
  ShortResearchObservationPortfolio,
  ShortResearchSignalRun,
  ShortResearchStatus,
  IntradayEtfWatchStatus,
  TrackedPosition,
  TrackedPositionDetail,
  TrackedPositionList
} from "@/lib/types";

type AssetType = "fund" | "etf";
type SortKey = "score" | "return_5d" | "return_20d" | "drawdown_low" | "risk_low" | "liquidity";
type OrderTimeBucket = "before_15" | "after_15" | "unknown";
type EtfUniverse = "default" | "all" | "illiquid";
type MobileTab = "ranking" | "detail" | "tracking" | "explanation";

const ASSET_PAGE_SIZE = 12;

const etfUniverseOptions: Array<{ key: EtfUniverse; label: string; description: string }> = [
  { key: "default", label: "默认精选", description: "只看数据新、历史够、成交额达标的 ETF。" },
  { key: "all", label: "全部可分析", description: "纳入有可用日线的 ETF，并显示排除原因。" },
  { key: "illiquid", label: "含低流动性", description: "把成交额偏低的 ETF 也放进来对比。" }
];

const baseSortOptions: Array<{ key: SortKey; label: string }> = [
  { key: "score", label: "综合排序" },
  { key: "return_5d", label: "近 5 日强" },
  { key: "return_20d", label: "近 20 日强" },
  { key: "drawdown_low", label: "回撤较小" },
  { key: "risk_low", label: "风险较低" }
];

const etfSortOptions: Array<{ key: SortKey; label: string }> = [
  ...baseSortOptions,
  { key: "liquidity", label: "成交额高" }
];

const assetModes: Record<
  AssetType,
  {
    label: string;
    shortLabel: string;
    title: string;
    description: string;
    poolLabel: string;
    classification: string;
    latestLabel: string;
    priceLabel: string;
    dataButton: string;
    trackingTitle: string;
    trackingDescription: string;
    trackingEmpty: string;
    detailEmpty: string;
    noResults: string;
    sourceSummary: string;
  }
> = {
  etf: {
    label: "场内 ETF（证券账户实时交易）",
    shortLabel: "场内 ETF",
    title: "场内 ETF 短线研究",
    description:
      "默认看证券账户可以买卖的场内 ETF，更适合一两周到两三个月的短线观察。这里用公开日线收盘价、成交额和风险标签做排序；价格比场外基金更及时，但页面数据仍可能延迟，不代表券商盘口实时价。",
    poolLabel: "ETF 池",
    classification: "场内 ETF（证券账户交易）",
    latestLabel: "最新交易日",
    priceLabel: "最新收盘价",
    dataButton: "拉取近 120 天 ETF 日线",
    trackingTitle: "标注你已经在证券账户买入的场内 ETF",
    trackingDescription:
      "ETF 追踪按公开行情估算，不连接券商账户。数据滞后、暂无 IOPV 这类问题只在网页提示；只有硬止损、移动止盈、趋势转弱、退出观察才发邮件提醒你人工判断。",
    trackingEmpty: "还没有追踪记录。左侧选择一只场内 ETF 后，点“我已买入，开始追踪”。",
    detailEmpty: "左侧选择一只 ETF 后，这里会显示走势、回撤、成交额、近期涨跌和解释。",
    noResults: "当前筛选条件下没有 ETF 结果。可以换一个方向，或先点击“拉取近 120 天 ETF 日线”。",
    sourceSummary:
      "说明：ETF 使用公开日线收盘价和成交额，适合证券账户短线研究；它不是券商盘口实时价，也不会自动下单。"
  },
  fund: {
    label: "支付宝场外基金（非实时净值）",
    shortLabel: "场外基金",
    title: "支付宝场外基金短线研究",
    description:
      "这里看支付宝可手动买入的场外基金，适合观察但不适合盘中即买即卖。数据来自公开基金净值，不是支付宝实时收益；15:00 后下单通常按下一交易日确认净值估算，名字带“ETF联接”的仍按场外基金处理。",
    poolLabel: "场外基金池",
    classification: "场外基金（非实时净值）",
    latestLabel: "最新净值日",
    priceLabel: "最新净值",
    dataButton: "拉取近 120 天基金净值",
    trackingTitle: "标注你已经在支付宝买入的场外基金",
    trackingDescription:
      "这里记录的是你在支付宝手动买入后的观察笔记。场外基金不是实时净值，系统每天检查公开数据，触发止盈观察、移动止盈、趋势转弱或明显风险时给你发邮件。",
    trackingEmpty: "还没有追踪记录。左侧选择一只场外基金后，点“我已买入，开始追踪”。",
    detailEmpty: "左侧选择一只场外基金后，这里会显示走势、回撤、近期涨跌和解释。",
    noResults: "当前筛选条件下没有场外基金结果。可以换一个方向，或先点击“拉取近 120 天基金净值”。",
    sourceSummary:
      "说明：场外基金使用公开基金净值。净值通常不是盘中实时数据；名字里有“ETF联接”的仍按场外基金净值确认。"
  }
};

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

function stringListMetric(metrics: Record<string, unknown>, key: string) {
  const value = metrics[key];
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function boolMetric(metrics: Record<string, unknown>, key: string) {
  return metrics[key] === true;
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
  return assetType === "etf" ? "场内 ETF" : "支付宝场外基金";
}

function assetTradingNote(assetType: string, name?: string) {
  if (assetType === "etf") {
    return "证券账户交易，使用公开日线收盘价和成交额估算；可做短线研究，但页面不是券商盘口实时价。";
  }
  if (name?.includes("ETF联接")) {
    return "支付宝可买；名字带 ETF联接，但仍是场外基金，非实时净值，按确认净值日估算。";
  }
  return "支付宝可买，非实时净值，按确认净值日估算。";
}

function formatTurnover(value: number | null) {
  if (value === null) {
    return "暂无";
  }
  if (value >= 100_000_000) {
    return `${(value / 100_000_000).toFixed(2)} 亿元`;
  }
  return `${(value / 10_000).toFixed(0)} 万元`;
}

function assetCount(status: ShortResearchStatus | undefined, assetType: AssetType) {
  return assetType === "etf" ? status?.etf_total_count ?? status?.etf_count ?? 0 : status?.fund_count ?? 0;
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

function costBasisSourceLabel(value: string | null | undefined) {
  if (value === "confirmed_shares_entry_price") {
    return "按成交份额 × 买入价";
  }
  if (value === "estimated_shares_entry_price") {
    return "按买入金额 ÷ 买入价估算份额";
  }
  if (value === "buy_amount_estimate") {
    return "按下单金额估算";
  }
  return "等待成本数据";
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
    return "卖出/减仓提醒";
  }
  if (alertType === "risk_warning") {
    return "数据质量提示";
  }
  if (alertType === "take_profit_watch") {
    return "止盈观察提醒";
  }
  if (alertType === "trailing_take_profit") {
    return "卖出/减仓提醒";
  }
  if (alertType === "trend_weakening") {
    return "卖出/减仓提醒";
  }
  if (alertType === "hard_stop") {
    return "止损提醒";
  }
  return alertType;
}

const EXIT_ALERT_TYPES = new Set(["exit_watch", "take_profit_watch", "trailing_take_profit", "trend_weakening", "hard_stop"]);

function isEmailExitAlert(alertType: string) {
  return EXIT_ALERT_TYPES.has(alertType);
}

function alertDeliveryLabel(alert: {
  alert_type: string;
  email_status: string;
  suppression_status: string | null;
  quote_time?: string | null;
}) {
  if (alert.suppression_status === "web_only" || !isEmailExitAlert(alert.alert_type)) {
    return "仅网页提示";
  }
  if (alert.suppression_status === "suppressed") {
    return "已去重";
  }
  if (alert.email_status === "sent") {
    return "已发邮件";
  }
  if (alert.quote_time === null || alert.quote_time === undefined) {
    return "等待数据";
  }
  return "仅网页提示";
}
function latestAlertTone(alert: {
  alert_type: string;
  email_status: string;
  suppression_status: string | null;
  quote_time?: string | null;
}) {
  const deliveryLabel = alertDeliveryLabel(alert);
  if (alert.alert_type === "hard_stop") {
    return "bg-rose-50 text-rose-800";
  }
  if (deliveryLabel === "仅网页提示") {
    return "bg-sky-50 text-sky-800";
  }
  if (deliveryLabel === "已去重") {
    return "bg-rose-50 text-rose-800";
  }
  if (deliveryLabel === "等待数据") {
    return "bg-stone-50 text-stone-700";
  }
  return "bg-amber-50 text-amber-900";
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
    return "等待价格";
  }
  return `${formatCurrency(snapshot.estimated_pnl)} / ${snapshot.estimated_pnl_pct.toFixed(2)}%`;
}

function percentOrWaiting(value: number | null) {
  return value === null ? "等待数据" : formatPercent(value);
}

function percentValueOrWaiting(value: number | null | undefined) {
  return value === null || value === undefined ? "暂无" : formatPercent(value);
}

function scoreOrWaiting(value: number | null) {
  return value === null ? "等待数据" : value.toFixed(1);
}

function priceSourceLabel(value: string | null | undefined) {
  if (value === "intraday_quote") {
    return "盘中公开行情";
  }
  if (value === "daily_close") {
    return "日线收盘价";
  }
  if (value === "manual_entry") {
    return "手填成交价";
  }
  return "暂无价格";
}

function marketStatusLabel(value: string | undefined) {
  if (value === "open") {
    return "交易中";
  }
  if (value === "closed") {
    return "非交易时段";
  }
  return "等待状态";
}

function formatDateTime(value: string | null | undefined) {
  if (!value) {
    return "暂无";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(new Date(value));
}

function formatUtcDateTime(value: string | null | undefined) {
  if (!value) {
    return "暂无";
  }
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value);
  const date = new Date(hasTimezone ? value : `${value}Z`);
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit"
  }).format(date);
}

function isAshareTradingPollWindow(timestamp: number) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23"
  }).formatToParts(new Date(timestamp));
  const value = (type: string) => parts.find((part) => part.type === type)?.value;
  const weekday = value("weekday");
  if (weekday === "Sat" || weekday === "Sun") {
    return false;
  }
  const minutes = Number(value("hour")) * 60 + Number(value("minute"));
  return (minutes >= 9 * 60 + 30 && minutes < 11 * 60 + 30) || (minutes >= 13 * 60 && minutes < 15 * 60);
}

function AssetPaginationBar({
  total,
  offset,
  visibleCount,
  isFetching,
  onPrevious,
  onNext
}: {
  total: number;
  offset: number;
  visibleCount: number;
  isFetching: boolean;
  onPrevious: () => void;
  onNext: () => void;
}) {
  if (total <= ASSET_PAGE_SIZE) {
    return null;
  }
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + visibleCount, total);
  const canPrevious = offset > 0;
  const canNext = offset + visibleCount < total;
  return (
    <div className="flex flex-col gap-3 rounded-[18px] bg-paper p-4 text-sm text-ink/65 md:flex-row md:items-center md:justify-between">
      <span>
        当前显示 {start} - {end} / {total} 只
      </span>
      <div className="flex gap-2">
        <button
          className="rounded-full border border-ink/10 bg-white px-4 py-2 font-semibold text-ink disabled:opacity-40"
          disabled={!canPrevious || isFetching}
          onClick={onPrevious}
        >
          上一页
        </button>
        <button
          className="rounded-full bg-ink px-4 py-2 font-semibold text-white disabled:opacity-40"
          disabled={!canNext || isFetching}
          onClick={onNext}
        >
          下一页
        </button>
      </div>
    </div>
  );
}
function TaskButton({
  children,
  variant = "secondary",
  disabled,
  onClick
}: {
  children: ReactNode;
  variant?: "primary" | "secondary";
  disabled?: boolean;
  onClick: () => void;
}) {
  const className =
    variant === "primary"
      ? "bg-ink text-white hover:bg-pine"
      : "border border-ink/10 bg-white text-ink hover:border-accent hover:text-accent";
  return (
    <button
      className={`rounded-full px-4 py-2 text-sm font-semibold transition disabled:opacity-60 ${className}`}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function WorkbenchMetric({
  label,
  value,
  tone = "bg-white text-ink"
}: {
  label: string;
  value: string;
  tone?: string;
}) {
  return (
    <div className={`rounded-[18px] border border-ink/10 px-4 py-3 ${tone}`}>
      <p className="text-[11px] font-semibold uppercase tracking-[0.18em] opacity-60">{label}</p>
      <p className="mt-1 text-base font-semibold leading-tight">{value}</p>
    </div>
  );
}

function SectionKicker({ eyebrow, title, description }: { eyebrow: string; title: string; description?: string }) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">{eyebrow}</p>
      <h2 className="mt-2 text-2xl font-semibold text-ink">{title}</h2>
      {description ? <p className="mt-2 text-sm leading-6 text-ink/65">{description}</p> : null}
    </div>
  );
}

export default function ShortTermPage() {
  const queryClient = useQueryClient();
  const detailColumnRef = useRef<HTMLDivElement | null>(null);
  const [detailColumnHeight, setDetailColumnHeight] = useState<number | null>(null);
  const [assetType, setAssetType] = useState<AssetType>("etf");
  const [etfUniverse, setEtfUniverse] = useState<EtfUniverse>("default");
  const [theme, setTheme] = useState("all");
  const [sort, setSort] = useState<SortKey>("score");
  const [keyword, setKeyword] = useState("");
  const [assetOffset, setAssetOffset] = useState(0);
  const [selected, setSelected] = useState<{ asset_type: "fund" | "etf"; code: string } | null>(null);
  const [mobileTab, setMobileTab] = useState<MobileTab>("ranking");
  const [lastResult, setLastResult] = useState<Record<string, unknown> | null>(null);
  const [trackingOpen, setTrackingOpen] = useState(false);
  const [trackingAmount, setTrackingAmount] = useState("3000");
  const [trackingDate, setTrackingDate] = useState(todayInputValue());
  const [trackingOrderTime, setTrackingOrderTime] = useState<OrderTimeBucket>(defaultOrderTimeBucket());
  const [trackingConfirmedNavDate, setTrackingConfirmedNavDate] = useState("");
  const [trackingConfirmedNav, setTrackingConfirmedNav] = useState("");
  const [trackingConfirmedShares, setTrackingConfirmedShares] = useState("");
  const [trackingNote, setTrackingNote] = useState("");
  const [pollClock, setPollClock] = useState(() => Date.now());
  const mode = assetModes[assetType];
  const sortOptions = assetType === "etf" ? etfSortOptions : baseSortOptions;
  const isEtfTradingPollWindow = assetType === "etf" && isAshareTradingPollWindow(pollClock);

  const status = useQuery({
    queryKey: ["short-research", "status"],
    queryFn: async () => (await api.get<ShortResearchStatus>("/api/short-research/status")).data
  });

  const assets = useQuery({
    queryKey: ["short-research", "assets", assetType, etfUniverse, theme, sort, keyword, assetOffset],
    queryFn: async () => {
      const params = new URLSearchParams();
      params.set("asset_type", assetType);
      params.set("limit", String(ASSET_PAGE_SIZE));
      params.set("offset", String(assetOffset));
      if (theme !== "all") {
        params.set("theme", theme);
      }
      if (keyword.trim()) {
        params.set("q", keyword.trim());
      }
      params.set("sort", sort);
      if (assetType === "etf") {
        params.set("universe", etfUniverse);
      }
      return (await api.get<ShortResearchAssetList>(`/api/short-research/assets?${params.toString()}`)).data;
    }
  });

  const observationPortfolio = useQuery({
    queryKey: ["short-research", "observation-portfolio", etfUniverse],
    enabled: assetType === "etf" && Boolean(assets.data?.items.length),
    queryFn: async () =>
      (
        await api.get<ShortResearchObservationPortfolio>(
          `/api/short-research/observation-portfolio?asset_type=etf&universe=${etfUniverse}`
        )
      ).data
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

  const intradayWatch = useQuery({
    queryKey: ["etf-quotes", "tracked"],
    enabled: assetType === "etf",
    queryFn: async () => (await api.get<IntradayEtfWatchStatus>("/api/etf-quotes/tracked")).data,
    refetchInterval: (query) => {
      const data = query.state.data as IntradayEtfWatchStatus | undefined;
      return isEtfTradingPollWindow && data?.market_status === "open" ? 30_000 : false;
    }
  });

  const shouldRefreshIntradayQueries = isEtfTradingPollWindow && intradayWatch.data?.market_status === "open";

  const trackedPositions = useQuery({
    queryKey: ["tracked-positions"],
    queryFn: async () => (await api.get<TrackedPositionList>("/api/tracked-positions")).data,
    refetchInterval: shouldRefreshIntradayQueries ? 30_000 : false
  });

  useEffect(() => {
    const timer = window.setInterval(() => setPollClock(Date.now()), 60_000);
    return () => window.clearInterval(timer);
  }, []);

  const previousTradingPollWindow = useRef(isEtfTradingPollWindow);
  useEffect(() => {
    if (!previousTradingPollWindow.current && isEtfTradingPollWindow) {
      void Promise.all([
        queryClient.invalidateQueries({ queryKey: ["etf-quotes", "tracked"] }),
        queryClient.invalidateQueries({ queryKey: ["tracked-positions"] })
      ]);
    }
    previousTradingPollWindow.current = isEtfTradingPollWindow;
  }, [isEtfTradingPollWindow, queryClient]);

  useEffect(() => {
    setAssetOffset(0);
  }, [assetType, etfUniverse, theme, sort, keyword]);

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

  useEffect(() => {
    if (assetType === "fund" && sort === "liquidity") {
      setSort("score");
    }
  }, [assetType, sort]);

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
          asset_type: assetType
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
          asset_type: assetType,
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
          asset_type: assetType,
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
        throw new Error(`请先选择一只${mode.shortLabel}`);
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
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["tracked-positions"] }),
        queryClient.invalidateQueries({ queryKey: ["etf-quotes"] })
      ]);
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
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["tracked-positions"] }),
        queryClient.invalidateQueries({ queryKey: ["etf-quotes"] })
      ]);
    }
  });

  const selectedAsset = selectedDetail.data?.asset;
  const advisorReport = selectedAsset?.advisor_report ?? null;
  const detailPoints = chartPoints(selectedDetail.data);
  const windowPoints = returnWindowChart(selectedAsset);
  const statusData = status.data;
  const currentLatestDate = statusData?.latest_data_date ?? assets.data?.as_of_date ?? null;
  const currentDataIssueCount =
    assetType === "etf"
      ? (statusData?.etf_data_stale_count ?? 0) + (statusData?.etf_failed_count ?? 0)
      : statusData?.data_issue_count ?? 0;
  const visibleAssets = assets.data?.items ?? [];
  const totalAssetCount = assets.data?.total ?? 0;
  const goToPreviousAssetPage = () => setAssetOffset((value) => Math.max(0, value - ASSET_PAGE_SIZE));
  const goToNextAssetPage = () => setAssetOffset((value) => value + ASSET_PAGE_SIZE);
  const observableCount = visibleAssets.filter((item) => item.conclusion === "短线观察").length;
  const highRiskCount = visibleAssets.filter((item) => item.conclusion === "高位观察").length;
  const dataIssues =
    statusData?.data_health
      .filter((item) => item.asset_type === assetType && (item.status !== "success" || item.is_stale))
      .slice(0, 6) ?? [];
  const activeTracked = (trackedPositions.data?.items ?? []).filter((item) => item.status === "active");
  const trackedByAsset = useMemo(() => {
    const map = new Map<string, TrackedPosition>();
    for (const item of activeTracked) {
      map.set(`${item.asset_type}-${item.asset_code}`, item);
    }
    return map;
  }, [activeTracked]);
  const selectedTracked = activeTracked.filter(
    (item) => selectedAsset && item.asset_type === selectedAsset.asset_type && item.asset_code === selectedAsset.code
  );
  const primaryTracked = selectedTracked[0] ?? null;
  const selectedAssetMetrics = selectedAsset?.metrics ?? {};
  const selectedAssetTrendScore = numericMetric(selectedAssetMetrics, "trend_score");
  const selectedAssetSource = selectedAsset?.source_note ?? "暂无";
  const selectedHoldingStatus = primaryTracked?.current_snapshot.current_label ?? "未持仓";
  const selectedHoldingDecision = primaryTracked?.exit_signal.label ?? "未触发持仓处理";
  const selectedLatestReason =
    primaryTracked?.exit_signal.reason ??
    primaryTracked?.exit_signal.reasons?.[0] ??
    primaryTracked?.latest_alert?.trigger_label ??
    "暂无持仓原因";
  const trackedDetail = useQuery({
    queryKey: ["tracked-position", primaryTracked?.id],
    enabled: primaryTracked !== null,
    queryFn: async () => (await api.get<TrackedPositionDetail>(`/api/tracked-positions/${primaryTracked?.id}`)).data
  });
  const trackingPoints = trackingChartPoints(trackedDetail.data);
  const trackingEntry = trackingPoints.find((point) => point.isEntry);
  const trackingHigh = trackingPoints.find((point) => point.isHigh);
  const trackingCurrent = trackingPoints.find((point) => point.isCurrent);
  const workbenchStyle = {
    "--short-term-detail-height": detailColumnHeight ? `${detailColumnHeight}px` : undefined
  } as CSSProperties & { "--short-term-detail-height"?: string };

  const mobileTabs: Array<{ id: MobileTab; label: string }> = [
    { id: "ranking", label: "榜单" },
    { id: "detail", label: "详情" },
    { id: "tracking", label: "追踪" },
    { id: "explanation", label: "说明" }
  ];

  const renderAssetStatusSummary = () => {
    if (!selectedAsset) {
      return null;
    }

    const trendWeakening = primaryTracked?.dynamic_thresholds?.trend_weakening
      ? "是"
      : primaryTracked?.dynamic_thresholds
      ? "否"
      : "暂无";
    const thresholds = primaryTracked?.dynamic_thresholds
      ? {
          hardStop: percentValueOrWaiting(primaryTracked.dynamic_thresholds.hard_stop_pct),
          profitStart: percentValueOrWaiting(primaryTracked.dynamic_thresholds.profit_start_pct),
          giveback: percentValueOrWaiting(primaryTracked.dynamic_thresholds.trailing_giveback_pct),
          trendWeakening
        }
      : null;

    return (
      <div className="mt-5 rounded-[20px] border border-ink/10 bg-white p-4">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">观察与持仓摘要</p>
        <div className="mt-3 grid gap-2 text-sm text-ink/70 md:grid-cols-2">
          <span>买入观察状态：{selectedAsset.conclusion}</span>
          <span>持仓状态：{selectedHoldingStatus}</span>
          <span>近5日涨跌：{percentMetric(selectedAssetMetrics, "return_5d")}</span>
          <span>近20日涨跌：{percentMetric(selectedAssetMetrics, "return_20d")}</span>
          <span>近60日涨跌：{percentMetric(selectedAssetMetrics, "return_60d")}</span>
          <span>60日回撤：{percentMetric(selectedAssetMetrics, "max_drawdown_60d")}</span>
          <span>波动（20日）：{percentMetric(selectedAssetMetrics, "volatility_20d")}</span>
          <span>趋势强度：{scoreOrWaiting(selectedAssetTrendScore)}</span>
          <span>数据来源：{selectedAssetSource}</span>
          <span>持仓处理状态：{selectedHoldingDecision}</span>
          <span>最新原因：{selectedLatestReason}</span>
        </div>
        <details className="mt-3 rounded-[14px] bg-paper px-3 py-2">
          <summary className="cursor-pointer text-sm font-semibold">动态阈值</summary>
          <p className="mt-2 text-xs text-ink/65">
            {thresholds
              ? `硬止损${thresholds.hardStop}；止盈起点${thresholds.profitStart}；回撤减仓${thresholds.giveback}；趋势减弱预警${thresholds.trendWeakening}`
              : "暂无追踪动态阈值"}
          </p>
          <p className="mt-2 text-xs leading-5 text-ink/55">
            动态线由公开行情和规则计算，AI只解释依据和风险，不改写止盈/止损线。
          </p>
        </details>
      </div>
    );
  };

  const isMobileLayout = () => typeof window !== "undefined" && window.matchMedia("(max-width: 1023px)").matches;

  const selectAsset = (item: ShortResearchAsset) => {
    setSelected({ asset_type: item.asset_type, code: item.code });
    if (isMobileLayout()) {
      setMobileTab("detail");
    }
  };

  useEffect(() => {
    if (!visibleAssets.length) {
      setSelected(null);
      setMobileTab("ranking");
    }
  }, [visibleAssets.length]);

  const mobileTrackingButtonLabel = trackingOpen ? "收起" : "我已买入，开始追踪";
  const rankingPanelContent = ({ compact }: { compact: boolean }) => {
    const cardPadding = compact ? "p-3" : "p-4";
    const cardGap = compact ? "gap-2" : "gap-3";
    const listGap = compact ? "space-y-2" : "space-y-3";
    return (
      <>
        <div className={`mb-5 flex flex-col ${compact ? "gap-1" : "gap-2"} md:flex-row md:items-end md:justify-between`}>
          <SectionKicker
            eyebrow="榜单"
            title={`${mode.shortLabel} 排序`}
            description="筛选、关键词与主题设置，快速定位候选标的。"
          />
          <span className="w-fit rounded-full bg-paper px-3 py-1 text-xs font-semibold text-ink/60">
            每页 12 条
          </span>
        </div>
        <div className={`grid gap-3 ${compact ? "" : "md:grid-cols-2"}`}>
          <div className="rounded-full border border-ink/10 bg-white px-4 py-2 text-sm font-semibold text-ink">{mode.classification}</div>
          <input
            className="rounded-full border border-ink/10 bg-white px-4 py-2 text-sm outline-none transition focus:border-accent"
            placeholder="搜索代码/名称"
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
                {item === "all" ? "全部主题" : item}
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
        {assetType === "etf" ? (
          <div className="mt-5 grid gap-3 md:grid-cols-3">
            {etfUniverseOptions.map((option) => {
              const active = etfUniverse === option.key;
              return (
                <button
                  key={option.key}
                  className={`rounded-[18px] border px-4 py-3 text-left transition ${
                    active ? "border-ink bg-ink text-white" : "border-ink/10 bg-white text-ink hover:border-accent"
                  }`}
                  onClick={() => setEtfUniverse(option.key)}
                >
                  <span className="text-sm font-semibold">{option.label}</span>
                  <span className={`mt-1 block text-xs leading-5 ${active ? "text-white/65" : "text-ink/55"}`}>
                    {option.description}
                  </span>
                </button>
              );
            })}
          </div>
        ) : null}
        <div className="mt-5">
          <AssetPaginationBar
            total={totalAssetCount}
            offset={assetOffset}
            visibleCount={visibleAssets.length}
            isFetching={assets.isFetching}
            onPrevious={goToPreviousAssetPage}
            onNext={goToNextAssetPage}
          />
        </div>
        <div className={`mt-5 ${listGap}`}>
          {assets.isLoading ? (
            <div className="rounded-[20px] border border-dashed border-ink/20 p-6 text-sm text-ink/55">正在加载榜单...</div>
          ) : null}
          {visibleAssets.map((item) => {
            const isSelected = selected?.asset_type === item.asset_type && selected.code === item.code;
            const exclusionReasons = stringListMetric(item.metrics, "default_exclusion_reasons");
            const defaultEligible = boolMetric(item.metrics, "default_display_eligible");
            const trackedForItem = trackedByAsset.get(`${item.asset_type}-${item.code}`);
            return (
              <button
                key={`${item.asset_type}-${item.code}`}
                className={`w-full ${compact ? "rounded-[16px]" : "rounded-[20px]"} ${cardPadding} text-left transition ${
                  isSelected ? "border-ink bg-ink text-white" : "border-ink/10 bg-white text-ink hover:border-accent"
                }`}
                onClick={() => selectAsset(item)}
              >
                <div className={`flex flex-col ${cardGap} md:flex-row md:items-start md:justify-between`}>
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
                    <p className={`mt-2 line-clamp-2 text-sm leading-6 ${isSelected ? "text-white/65" : "text-ink/60"}`}>
                      {rationaleText(item, "key_reason", "暂无")}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white text-ink" : "bg-ink text-white"}`}>
                      {item.total_score.toFixed(1)} 分
                    </span>
                    <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white/15 text-white" : conclusionTone(item.conclusion)}`}>
                      买入观察：{item.conclusion}
                    </span>
                    <span
                      className={`rounded-full px-3 py-1 text-xs font-semibold ${
                        isSelected ? "bg-white/15 text-white" : exitSignalTone(trackedForItem?.exit_signal.level ?? "none")
                      }`}
                    >
                      持仓处理：{trackedForItem?.exit_signal.label ?? "未追踪"}
                    </span>
                    {item.advisor_report ? (
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white/15 text-white" : advisorTone(item.advisor_report.action_label)}`}>
                        {item.advisor_report.action_label}
                      </span>
                    ) : null}
                  </div>
                </div>
                <div
                  className={`mt-4 grid gap-2 text-sm ${assetType === "etf" ? "md:grid-cols-6" : "md:grid-cols-5"} ${
                    isSelected ? "text-white/70" : "text-ink/60"
                  }`}
                >
                  <span>5日:{percentMetric(item.metrics, "return_5d")}</span>
                  <span>20日:{percentMetric(item.metrics, "return_20d")}</span>
                  <span>60日:{percentMetric(item.metrics, "return_60d")}</span>
                  <span>60日回撤:{percentMetric(item.metrics, "max_drawdown_60d")}</span>
                  {assetType === "etf" ? <span>20日换手:{formatTurnover(numericMetric(item.metrics, "average_turnover_20d"))}</span> : null}
                  <span>样本天数:{item.usable_days}</span>
                </div>
                {assetType === "etf" && !defaultEligible ? (
                  <p className={`mt-3 line-clamp-1 text-xs leading-5 ${isSelected ? "text-white/55" : "text-ink/45"}`}>
                    默认展示说明: {exclusionReasons.length ? exclusionReasons.join(" / ") : "数据不足未标注"}
                  </p>
                ) : null}
              </button>
            );
          })}
          {!assets.isLoading && (assets.data?.items ?? []).length === 0 ? (
            <div className="rounded-[20px] border border-dashed border-ink/20 p-6 text-sm leading-6 text-ink/55">
              {mode.noResults}
            </div>
          ) : null}
          <AssetPaginationBar
            total={totalAssetCount}
            offset={assetOffset}
            visibleCount={visibleAssets.length}
            isFetching={assets.isFetching}
            onPrevious={goToPreviousAssetPage}
            onNext={goToNextAssetPage}
          />
        </div>
      </>
    );
  };

  const renderMobileDetailPanel = () => {
    if (!selectedAsset) {
      return <div className="rounded-[20px] border border-dashed border-ink/20 p-6 text-sm leading-6 text-ink/55">{mode.detailEmpty}</div>;
    }
    return (
      <Panel className="rounded-[24px]">
        <div className="flex flex-col gap-3">
          <div>
            <p className="text-sm text-ink/50">
              {assetTypeLabel(selectedAsset.asset_type)} · {selectedAsset.trading_rule_label}
            </p>
            <h2 className="mt-2 text-2xl font-semibold text-ink">
              {selectedAsset.name}
              <span className="ml-2 text-lg font-normal text-ink/45">{selectedAsset.code}</span>
            </h2>
            <p className="mt-2 rounded-[14px] bg-paper px-4 py-3 text-sm leading-6 text-ink/65">
              {assetTradingNote(selectedAsset.asset_type, selectedAsset.name)}
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-[16px] bg-paper px-4 py-3">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">当前结论</p>
              <p className="mt-2 text-lg font-semibold text-ink">
                {selectedAsset.conclusion} · {selectedAsset.total_score.toFixed(1)} 分
              </p>
              <p className="mt-2 text-sm leading-6 text-ink/65">
                {rationaleText(selectedAsset, "key_reason", "暂无")}
              </p>
            </div>
            <div className="rounded-[16px] bg-ink p-4 text-white">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-white/50">持有建议</p>
              <p className="mt-2 text-sm leading-7 text-white/75">
                {rationaleText(selectedAsset, "holding_plan", "建议结合数据与风险控制后再操作。")}
              </p>
            </div>
          </div>
          <div className={`grid gap-2 text-sm ${assetType === "etf" ? "sm:grid-cols-5" : "sm:grid-cols-4"}`}>
            <StatPill label={mode.latestLabel} value={formatDate(selectedAsset.latest_date)} tone="bg-white text-ink" />
            <StatPill label={mode.priceLabel} value={selectedAsset.latest_value === null ? "暂无" : selectedAsset.latest_value.toFixed(4)} />
            <StatPill label="样本天数" value={`${selectedAsset.usable_days} 天`} tone="bg-accentSoft text-ink" />
            <StatPill label="样本标签" value={selectedAsset.conclusion} tone="bg-white text-ink" />
            {assetType === "etf" ? (
              <StatPill
                label="20日换手"
                value={formatTurnover(numericMetric(selectedAsset.metrics, "average_turnover_20d"))}
                tone="bg-white text-ink"
              />
            ) : null}
          </div>

          {renderAssetStatusSummary()}

          <div className="rounded-[20px] bg-ink text-white p-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-semibold">追踪入口</p>
                <p className="mt-1 text-sm leading-6 text-white/75">提交买入记录并开始追踪该标的，显示持仓与预警信息。</p>
              </div>
              <button
                className="rounded-full bg-accent px-5 py-3 text-sm font-semibold text-white transition hover:bg-pine"
                onClick={() => setTrackingOpen((value) => !value)}
              >
                {mobileTrackingButtonLabel}
              </button>
            </div>
            {trackingOpen ? (
              <div className="mt-4 grid gap-3 md:grid-cols-2">
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
                  买入日期
                  <input
                    className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent"
                    type="date"
                    value={trackingDate}
                    onChange={(event) => setTrackingDate(event.target.value)}
                  />
                </label>
                <label className="text-sm text-ink/65">
                  下单时段
                  <select
                    className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent"
                    value={trackingOrderTime}
                    onChange={(event) => setTrackingOrderTime(event.target.value as OrderTimeBucket)}
                  >
                    <option value="before_15">15:00 前</option>
                    <option value="after_15">15:00 后</option>
                    <option value="unknown">未知</option>
                  </select>
                </label>
                <label className="text-sm text-ink/65">
                  {assetType === "etf" ? "买入价格日（可选）" : "确认净值日（可选）"}
                  <input
                    className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent"
                    type="date"
                    value={trackingConfirmedNavDate}
                    onChange={(event) => setTrackingConfirmedNavDate(event.target.value)}
                  />
                </label>
                <label className="text-sm text-ink/65">
                  {assetType === "etf" ? "实际成交价（可选）" : "支付宝确认净值（可选）"}
                  <input
                    className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent"
                    inputMode="decimal"
                    placeholder={assetType === "etf" ? "例如：0.815" : "例如：7.3130"}
                    value={trackingConfirmedNav}
                    onChange={(event) => setTrackingConfirmedNav(event.target.value)}
                  />
                </label>
                <label className="text-sm text-ink/65">
                  {assetType === "etf" ? "实际成交份额（可选）" : "支付宝确认份额（可选）"}
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
                    placeholder="可选备注"
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
                保存追踪失败：{errorText(createTracking.error)}
              </p>
            ) : null}
          </div>
        </div>
      </Panel>
    );
  };

  const renderMobileTrackingPanel = () => (
    <Panel className="rounded-[24px]">
      {activeTracked.length ? (
        <div className="grid gap-3">
          {activeTracked.map((item) => (
            <div key={item.id} className="rounded-[18px] border border-ink/10 bg-white p-4">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-xs font-semibold text-accent">{trackingStatusLabel(item.status)}</p>
                  <h3 className="mt-1 text-lg font-semibold text-ink">
                    {item.asset_name}
                    <span className="ml-2 text-sm font-normal text-ink/45">{item.asset_code}</span>
                  </h3>
                  <p className="mt-1 text-xs text-ink/45">{assetTypeLabel(item.asset_type)}</p>
                </div>
                <span className={`shrink-0 rounded-full px-3 py-1 text-xs font-semibold ${conclusionTone(item.current_snapshot.current_label ?? "数据不足")}`}>
                  买入观察:{item.current_snapshot.current_label ?? "待确认"}
                </span>
              </div>
              <div className="mt-3 grid gap-1 text-sm text-ink/65 sm:grid-cols-2">
                <span>盈亏: {pnlText(item)}</span>
                <span>
                  持有天数: {item.holding_days === null ? "暂无" : `${item.holding_days} 天`}
                </span>
                <span>实时价: {item.current_snapshot.current_price?.toFixed(4) ?? "暂无"}</span>
                <span>
                  成本口径: {item.cost_basis === null ? "等待成本数据" : `${formatCurrency(item.cost_basis)} / ${costBasisSourceLabel(item.cost_basis_source)}`}
                </span>
                <span>持仓处理状态: {item.exit_signal.label}</span>
              </div>
              {item.latest_alert ? (
                <p className="mt-3 rounded-[12px] bg-paper px-3 py-2 text-xs text-ink/65">
                  最新预警: {alertTypeLabel(item.latest_alert.alert_type)} / {alertDeliveryLabel(item.latest_alert)}
                </p>
              ) : (
                <p className="mt-3 rounded-[12px] bg-paper px-3 py-2 text-xs leading-5 text-ink/65">
                  暂无追踪告警。数据质量/IOPV/流动性问题只在网页提示，不触发卖出邮件。
                </p>
              )}
              <button
                className="mt-4 rounded-full border border-ink/10 px-4 py-2 text-sm font-semibold text-ink transition hover:border-accent hover:text-accent disabled:opacity-60"
                disabled={closeTracking.isPending}
                onClick={() => closeTracking.mutate(item.id)}
              >
                停止追踪
              </button>
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-[20px] border border-dashed border-ink/20 bg-white p-5 text-sm leading-7 text-ink/55">
          {mode.trackingEmpty}
        </div>
      )}
    </Panel>
  );

  const renderMobileExplanationPanel = () => (
    <Panel className="rounded-[24px]">
      {selectedAsset ? (
        <div className="space-y-4">
          <p className="font-semibold text-ink">{selectedAsset.name} - 低频说明</p>
          <div className="rounded-[18px] bg-paper p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-accent">图表</p>
            <div className="mt-3 h-52">
              {detailPoints.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={detailPoints}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#eadfd2" />
                    <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={28} />
                    <YAxis tickLine={false} axisLine={false} width={56} domain={["dataMin", "dataMax"]} />
                    <Tooltip formatter={(value) => Number(value).toFixed(4)} />
                    <Area
                      type="monotone"
                      dataKey="value"
                      name={assetType === "etf" ? "净值" : "净值"}
                      stroke="#1f5c4b"
                      fill="#dce9df"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex h-full items-center justify-center rounded-[14px] bg-white text-sm text-ink/50">暂无图表数据</div>
              )}
            </div>
          </div>
          <div className="rounded-[18px] bg-paper p-4">
            <p className="font-semibold text-ink">AI/规则解释</p>
            <p className="mt-2 text-sm leading-6 text-ink/65">
              {advisorReport ? `${advisorReport.plain_summary}` : "暂无完整解释结果。"}
            </p>
          </div>
          <div className="rounded-[18px] border border-ink/10 bg-white p-4">
            <p className="font-semibold text-ink">标签原因</p>
            <p className="mt-2 text-sm leading-7 text-ink/65">
              {selectedAsset.conclusion === "数据不足" ? labelMeaning("数据不足") : rationaleText(selectedAsset, "label_meaning", labelMeaning(selectedAsset.conclusion))}
            </p>
          </div>
          <div className="rounded-[18px] border border-ink/10 bg-white p-4">
            <p className="font-semibold text-ink">观察组合</p>
            <p className="mt-2 text-sm leading-7 text-ink/65">
              {assetType === "etf" ? observationPortfolio.data?.note ?? "暂无观察组合说明" : "非 ETF 模式下暂不显示观察组合"}
            </p>
          </div>
          {dataIssues.length ? (
            <div className="rounded-[18px] border border-ink/10 bg-white p-4">
              <p className="font-semibold text-ink">数据问题</p>
              <div className="mt-2 grid gap-2 text-sm text-ink/65">
                {dataIssues.slice(0, 4).map((item) => (
                  <p key={`${item.asset_type}-${item.code}`}>
                    {item.name} · {assetTypeLabel(item.asset_type)} · {formatDate(item.latest_date)}
                  </p>
                ))}
              </div>
            </div>
          ) : null}
          <p className="rounded-[16px] bg-white/70 px-4 py-3 text-xs text-ink/60">
            {mode.sourceSummary}
          </p>
        </div>
      ) : (
        <div className="rounded-[20px] border border-dashed border-ink/20 p-6 text-sm leading-6 text-ink/55">
          先在“榜单”选择标的后查看说明信息
        </div>
      )}
    </Panel>
  );

  useEffect(() => {
    const node = detailColumnRef.current;
    if (!node || typeof ResizeObserver === "undefined") {
      setDetailColumnHeight(null);
      return;
    }

    const updateHeight = () => {
      const nextHeight = Math.ceil(node.getBoundingClientRect().height);
      setDetailColumnHeight((currentHeight) => (currentHeight === nextHeight ? currentHeight : nextHeight));
    };

    updateHeight();
    const observer = new ResizeObserver(updateHeight);
    observer.observe(node);
    window.addEventListener("resize", updateHeight);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", updateHeight);
    };
  }, []);

  return (
    <div className="space-y-6">
      <Panel className="rounded-[24px] bg-white/90">
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-start">
          <SectionKicker eyebrow="短线研究工作台" title={mode.title} description={mode.description} />
          <div className="flex flex-wrap gap-2 xl:justify-end">
            <TaskButton disabled={syncData.isPending} onClick={() => syncData.mutate()}>
              {syncData.isPending ? "准备数据中" : mode.dataButton}
            </TaskButton>
            <TaskButton variant="primary" disabled={runSignals.isPending} onClick={() => runSignals.mutate()}>
              {runSignals.isPending ? "排序生成中" : "生成短线排序"}
            </TaskButton>
            <TaskButton disabled={runAdvisor.isPending} onClick={() => runAdvisor.mutate()}>
              {runAdvisor.isPending ? "报告生成中" : "AI 研究说明"}
            </TaskButton>
          </div>
        </div>

        <div className="mt-5 flex flex-wrap gap-2 rounded-[20px] bg-paper p-2">
          {(["etf", "fund"] as AssetType[]).map((item) => {
            const isActive = assetType === item;
            return (
              <button
                key={item}
                className={`rounded-full px-4 py-2 text-sm font-semibold transition ${
                  isActive ? "bg-ink text-white shadow-sm" : "bg-white text-ink hover:text-accent"
                }`}
                onClick={() => {
                  setAssetType(item);
                  setTheme("all");
                  setSelected(null);
                }}
              >
                {assetModes[item].label}
              </button>
            );
          })}
        </div>

        <div className="mt-5 grid gap-3 md:grid-cols-3 xl:grid-cols-6">
          <WorkbenchMetric label={assetType === "etf" ? "ETF 全量池" : mode.poolLabel} value={`${assetCount(statusData, assetType)} 只`} />
          {assetType === "etf" ? (
            <WorkbenchMetric label="默认精选" value={`${statusData?.etf_default_display_count ?? 0} 只`} tone="bg-emerald-50 text-emerald-800" />
          ) : null}
          <WorkbenchMetric label="当前列表" value={`${visibleAssets.length}/${totalAssetCount} 只`} tone="bg-accentSoft/45 text-ink" />
          <WorkbenchMetric label={mode.latestLabel} value={formatDate(currentLatestDate)} />
          <WorkbenchMetric label="短线观察" value={`${observableCount} 只`} tone="bg-emerald-50 text-emerald-800" />
          <WorkbenchMetric label="高位观察" value={`${highRiskCount} 只`} tone="bg-rose-50 text-rose-800" />
          {assetType === "etf" ? (
            <WorkbenchMetric label="滞后/失败" value={`${statusData?.etf_data_stale_count ?? 0} / ${statusData?.etf_failed_count ?? 0} 只`} tone="bg-amber-50 text-amber-900" />
          ) : null}
          {assetType !== "etf" ? (
            <WorkbenchMetric label="数据异常" value={`${currentDataIssueCount} 只`} tone="bg-amber-50 text-amber-900" />
          ) : null}
        </div>

        {assetType === "etf" ? (
          <div className="mt-4 grid gap-3 rounded-[20px] border border-ink/10 bg-ink px-4 py-3 text-white md:grid-cols-4">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-white/50">盘中盯盘</p>
              <p className="mt-1 text-sm text-white/75">评分前 20 + 已追踪 ETF</p>
            </div>
            <div>
              <p className="text-xs text-white/50">市场状态</p>
              <p className="font-semibold">{marketStatusLabel(intradayWatch.data?.market_status)}</p>
            </div>
            <div>
              <p className="text-xs text-white/50">盯盘数量</p>
              <p className="font-semibold">{intradayWatch.data?.watched_count ?? 0} 只</p>
            </div>
            <div>
              <p className="text-xs text-white/50">最近运行</p>
              <p className="font-semibold">{formatUtcDateTime(intradayWatch.data?.latest_run?.finished_at)}</p>
            </div>
          </div>
        ) : null}

        {lastResult ? (
          <details className="mt-4 rounded-[18px] bg-paper px-4 py-3 text-sm text-ink/65">
            <summary className="cursor-pointer font-semibold text-ink">查看最近一次任务结果</summary>
            <pre className="mt-3 max-h-40 overflow-auto rounded-[14px] bg-white p-3 text-xs leading-5">
              {safeSummary(lastResult)}
            </pre>
          </details>
        ) : null}
      </Panel>

      {(syncData.isError || runSignals.isError || runAdvisor.isError || status.isError || intradayWatch.isError) && (
        <p className="rounded-[18px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {errorText(syncData.error ?? runSignals.error ?? runAdvisor.error ?? status.error ?? intradayWatch.error)}
        </p>
      )}

      <div className="lg:hidden">
        <div className="rounded-[18px] bg-white/90 p-1">
          <div className="grid grid-cols-4 gap-2">
            {mobileTabs.map((item) => (
              <button
                key={item.id}
                className={`rounded-full px-3 py-2 text-sm font-semibold transition ${
                  mobileTab === item.id ? "bg-ink text-white" : "bg-white text-ink hover:text-accent"
                }`}
                onClick={() => setMobileTab(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>
        <div className="mt-4">
          {mobileTab === "ranking" ? (
            <Panel className="rounded-[24px]">{rankingPanelContent({ compact: true })}</Panel>
          ) : null}
          {mobileTab === "detail" ? renderMobileDetailPanel() : null}
          {mobileTab === "tracking" ? renderMobileTrackingPanel() : null}
          {mobileTab === "explanation" ? renderMobileExplanationPanel() : null}
        </div>
      </div>

      <div className="hidden lg:grid gap-6 xl:grid-cols-[minmax(360px,0.78fr)_minmax(0,1.22fr)]" style={workbenchStyle}>
        <Panel className="rounded-[24px] xl:h-[var(--short-term-detail-height)] xl:overflow-auto">
          <div className="mb-5 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
            <SectionKicker
              eyebrow="买入观察榜单"
              title={`${mode.shortLabel}排序`}
              description="先扫分数、标签和关键指标；详细解释会在右侧展示。"
            />
            <span className="w-fit rounded-full bg-paper px-3 py-1 text-xs font-semibold text-ink/60">
              每页 12 只
            </span>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <div className="rounded-full border border-ink/10 bg-white px-4 py-2 text-sm font-semibold text-ink">
              {mode.classification}
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

          {assetType === "etf" ? (
            <div className="mt-5 grid gap-3 md:grid-cols-3">
              {etfUniverseOptions.map((option) => {
                const active = etfUniverse === option.key;
                return (
                  <button
                    key={option.key}
                    className={`rounded-[18px] border px-4 py-3 text-left transition ${
                      active ? "border-ink bg-ink text-white" : "border-ink/10 bg-white text-ink hover:border-accent"
                    }`}
                    onClick={() => setEtfUniverse(option.key)}
                  >
                    <span className="text-sm font-semibold">{option.label}</span>
                    <span className={`mt-1 block text-xs leading-5 ${active ? "text-white/65" : "text-ink/55"}`}>
                      {option.description}
                    </span>
                  </button>
                );
              })}
            </div>
          ) : null}

          <div className="mt-5">
            <AssetPaginationBar
              total={totalAssetCount}
              offset={assetOffset}
              visibleCount={visibleAssets.length}
              isFetching={assets.isFetching}
              onPrevious={goToPreviousAssetPage}
              onNext={goToNextAssetPage}
            />
          </div>

          <div className="mt-5 space-y-3">
            {assets.isLoading ? (
              <div className="rounded-[20px] border border-dashed border-ink/20 p-6 text-sm text-ink/55">
                正在读取短线研究池...
              </div>
            ) : null}
            {visibleAssets.map((item) => {
              const isSelected = selected?.asset_type === item.asset_type && selected.code === item.code;
              const exclusionReasons = stringListMetric(item.metrics, "default_exclusion_reasons");
              const defaultEligible = boolMetric(item.metrics, "default_display_eligible");
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
                      <p className={`mt-2 line-clamp-2 text-sm leading-6 ${isSelected ? "text-white/65" : "text-ink/60"}`}>
                        {rationaleText(item, "key_reason", "按近期趋势、风险和数据质量生成。")}
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
                  <div className={`mt-4 grid gap-2 text-sm ${assetType === "etf" ? "md:grid-cols-6" : "md:grid-cols-5"} ${isSelected ? "text-white/70" : "text-ink/60"}`}>
                    <span>近 5 日：{percentMetric(item.metrics, "return_5d")}</span>
                    <span>近 20 日：{percentMetric(item.metrics, "return_20d")}</span>
                    <span>近 60 日：{percentMetric(item.metrics, "return_60d")}</span>
                    <span>60 日回撤：{percentMetric(item.metrics, "max_drawdown_60d")}</span>
                    {assetType === "etf" ? (
                      <span>20 日成交额：{formatTurnover(numericMetric(item.metrics, "average_turnover_20d"))}</span>
                    ) : null}
                    <span>样本：{item.usable_days} 天</span>
                  </div>
                  {assetType === "etf" && !defaultEligible ? (
                    <p className={`mt-3 line-clamp-1 text-xs leading-5 ${isSelected ? "text-white/55" : "text-ink/45"}`}>
                      未进默认精选：{exclusionReasons.length ? exclusionReasons.join("；") : "数据质量或流动性未达标"}。
                    </p>
                  ) : null}
                </button>
              );
            })}
            {!assets.isLoading && (assets.data?.items ?? []).length === 0 ? (
              <div className="rounded-[20px] border border-dashed border-ink/20 p-6 text-sm leading-6 text-ink/55">
                {mode.noResults}
              </div>
            ) : null}
            <AssetPaginationBar
              total={totalAssetCount}
              offset={assetOffset}
              visibleCount={visibleAssets.length}
              isFetching={assets.isFetching}
              onPrevious={goToPreviousAssetPage}
              onNext={goToNextAssetPage}
            />
          </div>
        </Panel>

        <div ref={detailColumnRef} className="space-y-6 xl:sticky xl:top-6 xl:self-start">
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
                      {selectedAsset.asset_type === "etf"
                        ? " 评分和标签是买入观察状态；持仓处理状态会根据你的买入价、盘中价格、止盈/止损和趋势变化单独计算。"
                        : ""}
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

                <div className="mt-5 grid gap-3 md:grid-cols-[1fr_0.82fr]">
                  <div className="rounded-[20px] bg-paper p-4">
                    <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">当前结论</p>
                    <p className="mt-2 text-lg font-semibold text-ink">
                      {selectedAsset.conclusion} · 综合分 {selectedAsset.total_score.toFixed(1)}
                    </p>
                    <p className="mt-2 text-sm leading-7 text-ink/65">
                      {rationaleText(selectedAsset, "key_reason", "按近期趋势、回撤、波动、成交额和数据质量综合生成。")}
                    </p>
                  </div>
                  <div className="rounded-[20px] bg-ink p-4 text-white">
                    <p className="text-xs font-semibold uppercase tracking-[0.18em] text-white/50">持仓口径</p>
                    <p className="mt-2 text-sm leading-7 text-white/75">
                      评分高代表适合放入买入观察清单；如果你已经买入，是否卖出或减仓要看买入价、当前盈亏、趋势转弱、移动止盈和硬止损。
                    </p>
                  </div>
                </div>

                {renderAssetStatusSummary()}

                <div className="mt-5 rounded-[20px] border border-ink/10 bg-white p-4">
                  <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div>
                      <p className="text-sm font-semibold text-ink">我已买入，开始追踪</p>
                      <p className="mt-1 text-sm leading-6 text-ink/60">
                        {assetType === "etf"
                          ? "输入你在证券账户手动买入的金额和日期。系统按公开日线收盘价估算份额和盈亏，不连接券商账户。"
                          : "输入你手动买入的金额和下单时间。15:00 后下单会按下一条公开净值估算；支付宝已确认份额时可以手动填入。"}
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
                        {assetType === "etf" ? "买入价格日（可选）" : "确认净值日（可选）"}
                        <input
                          className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent"
                          type="date"
                          value={trackingConfirmedNavDate}
                          onChange={(event) => setTrackingConfirmedNavDate(event.target.value)}
                        />
                      </label>
                      <label className="text-sm text-ink/65">
                        {assetType === "etf" ? "实际成交价（可选）" : "支付宝确认净值（可选）"}
                        <input
                          className="mt-2 w-full rounded-2xl border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent"
                          inputMode="decimal"
                          placeholder={assetType === "etf" ? "例如：1.238" : "例如：7.3130"}
                          value={trackingConfirmedNav}
                          onChange={(event) => setTrackingConfirmedNav(event.target.value)}
                        />
                      </label>
                      <label className="text-sm text-ink/65">
                        {assetType === "etf" ? "实际成交份额（可选）" : "支付宝确认份额（可选）"}
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
                          placeholder={assetType === "etf" ? "例如：证券账户手动买入" : "例如：支付宝手动买入"}
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
                          暂无追踪曲线，等待公开数据更新。
                        </div>
                      )}
                    </div>
                    <div className="mt-3 grid gap-2 text-sm text-ink/65 md:grid-cols-3">
                      <span>确认点：{trackingEntry ? `${trackingEntry.label} / ${percentOrWaiting(trackingEntry.pnl)}` : "等待数据"}</span>
                      <span>最高点：{trackingHigh ? `${trackingHigh.label} / ${percentOrWaiting(trackingHigh.pnl)}` : "等待数据"}</span>
                      <span>当前点：{trackingCurrent ? `${trackingCurrent.label} / ${percentOrWaiting(trackingCurrent.pnl)}` : "等待数据"}</span>
                    </div>
                    <p className="mt-3 rounded-[16px] bg-paper px-4 py-3 text-sm leading-6 text-ink/65">
                      {primaryTracked.exit_signal.reason ?? "暂无持仓处理原因，继续观察公开数据。"}
                    </p>
                  </div>
                ) : null}

                <div className={`mt-5 grid gap-3 ${assetType === "etf" ? "md:grid-cols-5" : "md:grid-cols-4"}`}>
                  <StatPill label={mode.latestLabel} value={formatDate(selectedAsset.latest_date)} tone="bg-white text-ink" />
                  <StatPill label={mode.priceLabel} value={selectedAsset.latest_value === null ? "暂无" : selectedAsset.latest_value.toFixed(4)} />
                  <StatPill label="可用样本" value={`${selectedAsset.usable_days} 天`} tone="bg-accentSoft text-ink" />
                  {assetType === "etf" ? (
                    <StatPill
                      label="20 日成交额"
                      value={formatTurnover(numericMetric(selectedAsset.metrics, "average_turnover_20d"))}
                      tone="bg-white text-ink"
                    />
                  ) : null}
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
                          ? `${advisorReport.source === "llm" ? "AI生成" : "规则兜底"} · ${advisorReport.model_name} · ${formatDate(advisorReport.generated_at)}`
                          : "点击页面顶部“生成 AI 研究报告”后，会在这里显示多角度说明。没有报告时，排序和图表仍然正常可用。"}
                      </p>
                      <p className="mt-2 text-sm leading-6 text-ink/60">
                        排序、买入观察标签和持仓动态线由规则计算；AI只解释依据和风险，不直接决定买卖。
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
                            <Area type="monotone" dataKey="value" name={assetType === "etf" ? "收盘价" : "净值"} stroke="#1f5c4b" fill="#dce9df" />
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

                  {assetType === "etf" ? (
                    <div className="rounded-[20px] bg-paper p-4">
                      <p className="font-semibold text-ink">成交额</p>
                      <div className="mt-4 h-64">
                        {detailPoints.some((point) => point.turnover !== null) ? (
                          <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={detailPoints}>
                              <CartesianGrid stroke="#eadfd2" vertical={false} />
                              <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={28} />
                              <YAxis tickFormatter={(value) => `${Number(value).toFixed(1)}亿`} tickLine={false} axisLine={false} width={56} />
                              <Tooltip formatter={(value) => (value === null ? "暂无" : `${Number(value).toFixed(2)} 亿元`)} />
                              <Bar dataKey="turnover" name="成交额" fill="#e3b873" radius={[8, 8, 0, 0]} />
                            </BarChart>
                          </ResponsiveContainer>
                        ) : (
                          <div className="flex h-full items-center justify-center rounded-[18px] bg-white text-sm text-ink/50">
                            暂无成交额数据。
                          </div>
                        )}
                      </div>
                    </div>
                  ) : null}

                  <div className="rounded-[20px] bg-paper p-4">
                    <p className="font-semibold text-ink">样本和风险说明</p>
                    <div className="mt-4 h-64">
                      <div className="flex h-full flex-col justify-center rounded-[18px] bg-white p-5 text-sm leading-7 text-ink/60">
                        <p>{selectedAsset.sample_level}</p>
                        <p className="mt-2">
                          风险标签：{selectedAsset.risk_flags.length ? selectedAsset.risk_flags.join("、") : "暂未触发主要风险标签"}
                        </p>
                        <p className="mt-2">数据来源：{selectedAsset.source_note}</p>
                        <p className="mt-2">
                          交易口径：
                          {assetType === "etf"
                            ? "证券账户场内 ETF，按公开日线收盘价估算；页面不是券商盘口实时价。"
                            : "支付宝场外基金，按确认净值日估算，不是盘中实时价格。"}
                        </p>
                      </div>
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <div className="rounded-[20px] border border-dashed border-ink/20 p-8 text-sm leading-6 text-ink/55">
                {mode.detailEmpty}
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

      <div className="hidden lg:block">
      <Panel className="rounded-[24px]">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <SectionKicker eyebrow="我的持仓观察" title={mode.trackingTitle} description={mode.trackingDescription} />
          <div className="rounded-[18px] bg-paper px-4 py-3 text-sm leading-6 text-ink/65">
            收件邮箱：{trackedPositions.data?.recipient_email ?? "19535838578@163.com"}
            <br />
            邮件通道：{trackedPositions.data?.email_configured ? "已配置" : "未配置授权码"}
          </div>
        </div>

        <div className="mt-5 grid gap-4 xl:grid-cols-3">
          {activeTracked.map((item) => (
            <div key={item.id} className="rounded-[22px] border border-ink/10 bg-white p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold text-accent">{trackingStatusLabel(item.status)}</p>
                  <h3 className="mt-1 text-xl font-semibold text-ink">
                    {item.asset_name}
                    <span className="ml-2 text-sm font-normal text-ink/45">{item.asset_code}</span>
                  </h3>
                  <p className="mt-1 text-xs text-ink/45">{assetTypeLabel(item.asset_type)}</p>
                </div>
                <span className={`shrink-0 rounded-full px-3 py-1 text-xs font-semibold ${conclusionTone(item.current_snapshot.current_label ?? "数据不足")}`}>
                  买入观察：{item.current_snapshot.current_label ?? "等待排序"}
                </span>
              </div>

              <div className="mt-4 grid gap-2 text-sm text-ink/65">
                <span>下单：{formatCurrency(item.buy_amount)} / {formatDate(item.buy_date)}（{orderTimeBucketLabel(item.order_time_bucket)}）</span>
                <span>{item.asset_type === "etf" ? "买入价格日" : "确认净值日"}：{formatDate(item.confirmed_nav_date ?? item.entry_price_date)}</span>
                <span>{item.asset_type === "etf" ? "买入价" : "确认净值"}：{item.confirmed_nav?.toFixed(4) ?? item.entry_price?.toFixed(4) ?? "等待价格"}</span>
                <span>当前价：{item.current_snapshot.current_price?.toFixed(4) ?? "暂无"}</span>
                <span>
                  成本口径：{item.cost_basis === null ? "等待成本数据" : `${formatCurrency(item.cost_basis)} / ${costBasisSourceLabel(item.cost_basis_source)}`}
                </span>
                <span>持有：{item.holding_days === null ? "等待数据" : `${item.holding_days} 天`}</span>
                <span>{item.confirmed_shares === null ? "估算份额" : "确认份额"}：{item.estimated_shares === null ? "等待价格" : item.estimated_shares.toFixed(2)}</span>
                <span className={pnlTone(item.current_snapshot.estimated_pnl)}>估算盈亏：{pnlText(item)}</span>
              </div>

              <div className={`mt-4 rounded-[16px] p-3 text-sm leading-6 ${exitSignalTone(item.exit_signal.level)}`}>
                <p className="text-xs font-semibold opacity-75">持仓处理状态</p>
                <p className="font-semibold">{item.exit_signal.label}</p>
                <p className="mt-1">{item.exit_signal.reason ?? "暂无持仓处理原因，继续观察公开数据。"}</p>
              </div>

              {item.latest_alert ? (
                <div className={`mt-4 rounded-[16px] p-3 text-sm leading-6 ${latestAlertTone(item.latest_alert)}`}>
                  <p className="font-semibold">
                    {alertTypeLabel(item.latest_alert.alert_type)} · {alertDeliveryLabel(item.latest_alert)}
                  </p>
                  <p>{item.latest_alert.reasons[0] ?? item.latest_alert.trigger_label}</p>
                  <p className="mt-1 text-xs">
                    {item.latest_alert.alert_source ? `来源：${priceSourceLabel(item.latest_alert.alert_source)}；` : ""}
                    行情时间：{formatDateTime(item.latest_alert.quote_time)}
                    {item.latest_alert.email_error_message ? `；${item.latest_alert.email_error_message}` : ""}
                  </p>
                </div>
              ) : (
                <p className="mt-4 rounded-[16px] bg-paper p-3 text-sm leading-6 text-ink/55">
                  暂无可展示告警。数据质量/IOPV/流动性问题只在网页提示，不触发卖出邮件。
                </p>
              )}

              <details className="mt-3 rounded-[16px] bg-paper p-3 text-xs leading-5 text-ink/60">
                <summary className="cursor-pointer font-semibold text-ink">更多风控数据</summary>
                <div className="mt-2 grid gap-1">
                  <span>最高盈利：{percentOrWaiting(item.max_profit_pct)}</span>
                  <span>高点回吐：{percentOrWaiting(item.profit_giveback_pct)}</span>
                  {item.asset_type === "etf" ? (
                    <>
                      <span>价格来源：{priceSourceLabel(item.intraday_snapshot?.price_source)}</span>
                      <span>行情时间：{formatDateTime(item.intraday_snapshot?.quote_time)}</span>
                      <span>
                        动态止损线：
                        {item.dynamic_thresholds?.hard_stop_pct === null || item.dynamic_thresholds?.hard_stop_pct === undefined
                          ? "等待数据"
                          : formatPercent(item.dynamic_thresholds.hard_stop_pct)}
                      </span>
                      <span>
                        止盈启动线：
                        {item.dynamic_thresholds?.profit_start_pct === null || item.dynamic_thresholds?.profit_start_pct === undefined
                          ? "等待数据"
                          : formatPercent(item.dynamic_thresholds.profit_start_pct)}
                      </span>
                      <span>
                        移动止盈回吐线：
                        {item.dynamic_thresholds?.trailing_giveback_pct === null ||
                        item.dynamic_thresholds?.trailing_giveback_pct === undefined
                          ? "等待数据"
                          : formatPercent(item.dynamic_thresholds.trailing_giveback_pct)}
                      </span>
                      <span>动态线由规则计算，AI只做解释，不改写这些线。</span>
                      <span>
                        买卖价差：
                        {item.intraday_snapshot?.spread_pct === null || item.intraday_snapshot?.spread_pct === undefined
                          ? "暂无"
                          : formatPercent(item.intraday_snapshot.spread_pct)}
                      </span>
                      <span>
                        折溢价：
                        {item.intraday_snapshot?.premium_discount_pct === null ||
                        item.intraday_snapshot?.premium_discount_pct === undefined
                          ? "暂无"
                          : formatPercent(item.intraday_snapshot.premium_discount_pct)}
                      </span>
                    </>
                  ) : null}
                  {item.recent_intraday_alerts.slice(0, 3).map((alert) => (
                    <span key={alert.id}>
                      {formatDateTime(alert.quote_time)} · {alertTypeLabel(alert.alert_type)} · {alertDeliveryLabel(alert)}
                    </span>
                  ))}
                </div>
              </details>

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
              {mode.trackingEmpty}
            </div>
          ) : null}
        </div>
      </Panel>

      {assetType === "etf" ? (
        <Panel className="rounded-[24px] bg-white/70">
          <details>
            <summary className="cursor-pointer text-lg font-semibold text-ink">
              ETF 观察组合参考
              <span className="ml-3 rounded-full bg-accentSoft/60 px-3 py-1 text-xs text-ink">
                现金比例 {observationPortfolio.data ? formatPercent(observationPortfolio.data.cash_weight * 100) : "暂无"}
              </span>
            </summary>
            <p className="mt-2 text-sm leading-6 text-ink/60">
              这里只是研究用目标权重：风险高时提高现金比例，不连接证券账户，不自动下单。
            </p>
            <div className="mt-5 grid gap-3 lg:grid-cols-3">
              {(observationPortfolio.data?.items ?? []).map((item) => (
                <div key={item.code} className="rounded-[18px] border border-ink/10 bg-white p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-ink">{item.name}</p>
                      <p className="mt-1 text-xs text-ink/45">{item.code} · {formatDate(item.data_date)}</p>
                    </div>
                    <span className="rounded-full bg-ink px-3 py-1 text-xs font-semibold text-white">
                      {formatPercent(item.target_weight * 100)}
                    </span>
                  </div>
                  <p className="mt-3 text-sm text-ink/65">综合分 {item.score.toFixed(1)} · {item.conclusion}</p>
                  <p className="mt-3 rounded-[14px] bg-paper px-3 py-2 text-xs leading-5 text-ink/55">
                    风险：{item.risk_reasons.join("；")}
                  </p>
                </div>
              ))}
            </div>
            {!observationPortfolio.isLoading && !(observationPortfolio.data?.items ?? []).length ? (
              <p className="mt-4 rounded-[18px] bg-paper px-4 py-3 text-sm text-ink/55">
                暂无可用观察组合。先更新 ETF 数据，或切到“全部可分析”查看低流动性样本。
              </p>
            ) : null}
            <p className="mt-4 text-xs leading-5 text-ink/50">
              {observationPortfolio.data?.note ?? "观察组合只用于手动研究参考。"}
            </p>
          </details>
        </Panel>
      ) : null}

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
        {mode.sourceSummary}
        页面里的排序、标签和 AI 说明都用于研究观察，不代表未来收益，也不会触发真实操作；
        真实买卖仍需要你在{assetType === "etf" ? "证券账户" : "支付宝"}手动确认。
      </p>
      </div>
    </div>
  );
}
