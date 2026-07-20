"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { isAxiosError } from "axios";
import { useSearchParams } from "next/navigation";
import type { ReactNode } from "react";
import { Suspense, useEffect, useMemo, useRef, useState } from "react";
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
import { useAuth } from "../auth-provider";
import { api } from "@/lib/api";
import { formatCurrency, formatDate, formatPercent } from "@/lib/format";
import type {
  EtfOptimizedAllocation,
  ShortResearchAsset,
  ShortResearchAssetDetail,
  ShortResearchAssetList,
  ShortResearchObservationPortfolio,
  ShortResearchSignalRun,
  ShortResearchStatus,
  IntradayEtfLiveRankingItem,
  IntradayEtfLiveRankingList,
  TrackedPosition,
  TrackedPositionActionExecutionInput,
  TrackedPositionActionSummary,
  TrackedPositionActionTransitionRequest,
  TrackedPositionActionTransitionResponse,
  TrackedPositionAlertAuditList,
  TrackedPositionDetail,
  TrackedPositionExecutionPriceSource,
  TrackedPositionList
} from "@/lib/types";

type AssetType = "fund" | "etf";
type RankingSurface = "research" | "actionable";
type SortKey = "score" | "opportunity" | "return_5d" | "return_20d" | "drawdown_low" | "risk_low" | "liquidity";
type OrderTimeBucket = "before_15" | "after_15" | "unknown";
type MobileTab = "ranking" | "detail" | "tracking" | "explanation";
type RankedAssetItem = ShortResearchAsset | IntradayEtfLiveRankingItem;
type RankedAssetResponse = ShortResearchAssetList | IntradayEtfLiveRankingList;
type FreshIntradayQuote = NonNullable<IntradayEtfLiveRankingItem["quote"]> & { change_percent: number };
type LabelFilterKey = "observation" | "entry" | "tracking";
type LabelFilterState = Record<LabelFilterKey, string[]>;
type TrackedPositionPatchPayload = {
  buy_amount?: number;
  buy_date?: string;
  order_time_bucket?: OrderTimeBucket;
  confirmed_nav_date?: string;
  confirmed_nav?: number;
  confirmed_shares?: number;
  note?: string;
};

type ActionExecutionDraft = {
  executedAt: string;
  quantity: string;
  price: string;
  priceSource: TrackedPositionExecutionPriceSource;
  fees: string;
  resultingShares: string;
  closeFact: boolean;
};
type ActionTransitionMutationRequest = {
  positionId: number;
  actionId: number;
  expectedPositionStateVersion: number;
  transition: TrackedPositionActionTransitionRequest["transition"];
  execution?: TrackedPositionActionExecutionInput;
};

const ASSET_PAGE_SIZE = 12;
const emptyLabelFilters: LabelFilterState = { observation: [], entry: [], tracking: [] };

const executionPriceSourceOptions: Array<{
  value: TrackedPositionExecutionPriceSource;
  label: string;
}> = [
  { value: "owner_reported", label: "用户自行报告" },
  { value: "broker_confirmation", label: "券商确认" },
  { value: "trade_statement", label: "成交回单" },
  { value: "owner_broker_statement", label: "用户提供券商交割单" }
];

const baseSortOptions: Array<{ key: SortKey; label: string }> = [
  { key: "score", label: "综合排序" },
  { key: "return_5d", label: "近 5 日强" },
  { key: "return_20d", label: "近 20 日强" },
  { key: "drawdown_low", label: "回撤较小" },
  { key: "risk_low", label: "风险较低" }
];

const etfSortOptions: Array<{ key: SortKey; label: string }> = [
  { key: "opportunity", label: "综合榜单" },
  { key: "score", label: "盘中买点榜" }
];

const labelFilterGroups: Array<{
  key: LabelFilterKey;
  title: string;
  options: string[];
}> = [
  { key: "observation", title: "买入观察", options: ["短线观察", "高位观察", "谨慎观察", "不适合短线", "数据不足"] },
  { key: "entry", title: "今日买点", options: ["健康回踩", "趋势延续", "冲高别追", "跌破等待", "放量转弱", "午休", "休市", "行情滞后", "数据不足"] },
  { key: "tracking", title: "持仓状态", options: ["我已持仓", "触发提醒", "仅网页提示"] }
];

function appendLabelFilterParams(params: URLSearchParams, filters: LabelFilterState) {
  if (filters.observation.length) {
    params.set("observation_labels", filters.observation.join(","));
  }
  if (filters.entry.length) {
    params.set("entry_labels", filters.entry.join(","));
  }
  if (filters.tracking.length) {
    params.set("tracking_states", filters.tracking.join(","));
  }
}

const assetModes: Record<
  AssetType,
  {
    label: string;
    shortLabel: string;
    title: string;
    description?: string;
    poolLabel: string;
    classification: string;
    latestLabel: string;
    priceLabel: string;
    dataButton: string;
    trackingTitle: string;
    trackingDescription?: string;
    trackingEmpty: string;
    detailEmpty: string;
    noResults: string;
  }
> = {
  etf: {
    label: "场内 ETF（证券账户实时交易）",
    shortLabel: "场内 ETF",
    title: "场内 ETF 短线研究",
    poolLabel: "ETF 池",
    classification: "场内 ETF（证券账户交易）",
    latestLabel: "日线基准日",
    priceLabel: "日线收盘价",
    dataButton: "拉取近 120 天 ETF 日线",
    trackingTitle: "标注你已经在证券账户买入的场内 ETF",
    trackingEmpty: "还没有追踪记录。左侧选择一只场内 ETF 后，点“我已买入，开始追踪”。",
    detailEmpty: "左侧选择一只 ETF 后，这里会显示日线走势、盘中价、回撤、成交额、近期涨跌和解释。",
    noResults: "当前筛选条件下没有 ETF 结果。可以换一个方向，或先更新日线数据并生成短线排序。"
  },
  fund: {
    label: "支付宝场外基金（非实时净值）",
    shortLabel: "场外基金",
    title: "支付宝场外基金短线研究",
    poolLabel: "场外基金池",
    classification: "场外基金（非实时净值）",
    latestLabel: "最新净值日",
    priceLabel: "最新净值",
    dataButton: "拉取近 120 天基金净值",
    trackingTitle: "标注你已经在支付宝买入的场外基金",
    trackingEmpty: "还没有追踪记录。左侧选择一只场外基金后，点“我已买入，开始追踪”。",
    detailEmpty: "左侧选择一只场外基金后，这里会显示走势、回撤、近期涨跌和解释。",
    noResults: "当前筛选条件下没有场外基金结果。可以换一个方向，或先点击“拉取近 120 天基金净值”。"
  }
};

const fallbackThemes = [
  "科技",
  "AI",
  "机器人",
  "创新药",
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

type LabelValidationWindow = {
  sample_count?: number;
  unique_signal_date_count?: number;
  effective_sample_count?: number;
  asset_count?: number;
  avg_return?: number | null;
  median_return?: number | null;
  worst_forward_drawdown?: number | null;
  max_drawdown?: number | null;
  win_rate?: number | null;
  coverage?: number | null;
  asset_coverage?: number | null;
  turnover?: number | null;
  cost_per_round_trip?: number | null;
  paired_sample_count?: number;
  paired_excess_return_mean?: number | null;
  paired_excess_return_ci_95?: [number, number] | null;
  effect_direction?: string;
  sample_sufficiency?: string;
  endpoint_type?: "primary" | "exploratory" | string;
  confidence?: string;
  insufficient_sample?: boolean;
};

type LabelValidationGroup = {
  label?: string;
  entry_timing_label?: string;
  key?: string;
  windows?: Record<string, LabelValidationWindow>;
};

type ScoreBucketValidationGroup = LabelValidationGroup & {
  group_type?: string;
  selected_count?: number;
  selected_codes?: string[];
};

function labelValidationGroups(statusData: ShortResearchStatus | undefined): LabelValidationGroup[] {
  const validation = statusData?.label_validation as { groups?: unknown } | undefined;
  return Array.isArray(validation?.groups) ? (validation.groups as LabelValidationGroup[]) : [];
}

function scoreBucketValidationGroups(statusData: ShortResearchStatus | undefined): ScoreBucketValidationGroup[] {
  const validation = statusData?.score_bucket_validation as { groups?: unknown } | undefined;
  return Array.isArray(validation?.groups) ? (validation.groups as ScoreBucketValidationGroup[]) : [];
}

function labelValidationLine(group: LabelValidationGroup | undefined, window: "5" | "10") {
  const item = group?.windows?.[window];
  if (!item || !item.sample_count) {
    return `${window}日：样本不足`;
  }
  const median = item.median_return === null || item.median_return === undefined ? "暂无" : formatPercent(item.median_return * 100);
  const drawdownValue = item.worst_forward_drawdown ?? item.max_drawdown;
  const drawdown = drawdownValue === null || drawdownValue === undefined ? "暂无" : formatPercent(drawdownValue * 100);
  const winRate = item.win_rate === null || item.win_rate === undefined ? "暂无" : formatPercent(item.win_rate * 100);
  return `${window}日：样本 ${item.sample_count}，中位收益 ${median}，胜率 ${winRate}，最差回撤 ${drawdown}`;
}

function scoreBucketWindowText(group: ScoreBucketValidationGroup, window: "5" | "10") {
  const item = group.windows?.[window];
  if (!item || !item.sample_count) {
    return "样本不足";
  }
  const median = item.median_return === null || item.median_return === undefined ? "暂无" : formatPercent(item.median_return * 100);
  const winRate = item.win_rate === null || item.win_rate === undefined ? "暂无" : formatPercent(item.win_rate * 100);
  const paired =
    item.paired_excess_return_mean === null || item.paired_excess_return_mean === undefined
      ? "暂无"
      : formatPercent(item.paired_excess_return_mean * 100);
  return item.endpoint_type === "primary" ? `净中位 ${median} / 配对超额 ${paired}` : `${median} / ${winRate}`;
}

function scoreBucketSampleText(group: ScoreBucketValidationGroup) {
  const window = group.windows?.["5"] ?? group.windows?.["10"] ?? group.windows?.["1"];
  const selectedCount = group.selected_count ?? group.selected_codes?.length;
  const sampleCount = window?.sample_count ?? 0;
  const assetCount = window?.asset_count ?? 0;
  if (selectedCount !== undefined) {
    return `${sampleCount} 日期 / ${assetCount || selectedCount} 资产`;
  }
  return `${sampleCount}`;
}

function scoreBucketUnavailableText(summary: Record<string, unknown>) {
  const reason = typeof summary.unavailable_reason === "string" ? summary.unavailable_reason : "";
  switch (reason) {
    case "waiting_signal_generation":
      return "尚无通过发布门槛的 final_score_v3 历史快照；需先完成复权价、盘中报价及综合分覆盖，且不使用旧分数或原始价格补算。";
    case "no_available_final_decision_score":
      return "已有历史运行，但其中没有可决策的 final_score_v3 排名分；不使用旧分数或原始价格补算。";
    default:
      return Object.keys(summary).length
        ? "本次严格验证尚未形成完整样本；等待 T+1 入场与对应持有窗口走完，不使用旧分数或原始价格补算。"
        : "尚未形成可验证的 final_score_v3 历史样本；生产前瞻与 PIT 研究回放会分开累计。";
  }
}

function scoreBucketPrimaryEndpointText(group: ScoreBucketValidationGroup | undefined) {
  const window = group?.windows?.["5"];
  if (!window) {
    return "等待 Top 10 5 日配对样本。";
  }
  const interval = window.paired_excess_return_ci_95;
  const intervalText = interval ? `${formatPercent(interval[0] * 100)} 至 ${formatPercent(interval[1] * 100)}` : "暂无";
  const effect = window.effect_direction ?? "insufficient";
  const label =
    effect === "supportive" ? "支持" : effect === "negative" ? "负向" : effect === "inconclusive" ? "不确定" : "样本不足";
  const coverage = window.asset_coverage ?? window.coverage;
  const coverageText = coverage === null || coverage === undefined ? "暂无" : formatPercent(coverage * 100);
  const cost = window.cost_per_round_trip;
  const costText = cost === null || cost === undefined ? "暂无" : formatPercent(cost * 100);
  return `主终点：Top 10 5 日相对 all_scored 的配对净超额，${label}；95% 区间 ${intervalText}；独立日期 ${window.paired_sample_count ?? 0}；数据覆盖 ${coverageText}；双边成本 ${costText}。`;
}

function validationConfidenceLabel(confidence: string | undefined) {
  if (confidence === "sufficient") {
    return "样本较足";
  }
  if (confidence === "limited") {
    return "样本有限";
  }
  return "样本不足";
}

function sampleQualityLabel(evidence: ShortResearchAsset["validation_evidence"] | null | undefined) {
  const sampleQuality = evidence?.sample_quality;
  if (!sampleQuality) {
    return "无样本质量说明";
  }
  const excluded = sampleQuality.excluded_count_total ?? 0;
  const rawReasons = sampleQuality.exclusion_reasons;
  const reasons = Array.isArray(rawReasons) ? rawReasons : Object.keys(rawReasons ?? {});
  if (excluded > 0 && reasons.length) {
    return `排除 ${excluded} 条不可决策样本：${reasons.slice(0, 2).join("、")}`;
  }
  return `可用样本 ${sampleQuality.sample_count_total ?? evidence?.sample_count ?? 0}`;
}

function evidenceTrack(asset: ShortResearchAsset | null | undefined, key: "historical_replay" | "forward_live") {
  const evidence = asset?.validation_evidence;
  return evidence?.evidence_tracks?.[key] ?? evidence?.[key] ?? null;
}

function evidenceHorizon(evidence: ShortResearchAsset["validation_evidence"] | null | undefined, window = "5") {
  if (!evidence?.horizons) {
    return null;
  }
  return evidence.horizons[window] ?? null;
}

function validationTrackText(
  evidence: ShortResearchAsset["validation_evidence"] | null | undefined,
  label: string,
  options: { replay?: boolean } = {}
) {
  const replay = options.replay ?? false;
  if (!evidence) {
    return replay ? `${label}：还没运行历史回放。` : `${label}：真实前瞻样本正在积累。`;
  }
  const horizon = evidenceHorizon(evidence, "5");
  const sampleCount = horizon?.sample_count ?? evidence.sample_count ?? 0;
  if (!sampleCount) {
    return replay ? `${label}：样本不足，先运行历史回放。` : `${label}：真实前瞻样本不足，继续积累。`;
  }
  const medianReturn =
    (horizon?.median_return ?? evidence.median_return) === null || (horizon?.median_return ?? evidence.median_return) === undefined
      ? "暂无"
      : formatPercent((horizon?.median_return ?? evidence.median_return ?? 0) * 100);
  const winRate =
    (horizon?.win_rate ?? evidence.win_rate) === null || (horizon?.win_rate ?? evidence.win_rate) === undefined
      ? "暂无"
      : formatPercent((horizon?.win_rate ?? evidence.win_rate ?? 0) * 100);
  const drawdown =
    horizon?.worst_forward_drawdown === null || horizon?.worst_forward_drawdown === undefined
      ? "暂无"
      : formatPercent(horizon.worst_forward_drawdown * 100);
  const coverage =
    horizon?.coverage === null || horizon?.coverage === undefined ? "暂无" : formatPercent(horizon.coverage * 100);
  return `${label}：${validationConfidenceLabel(horizon?.confidence ?? evidence.confidence)}，5日样本 ${sampleCount}，中位收益 ${medianReturn}，胜率 ${winRate}，最差回撤 ${drawdown}，覆盖率 ${coverage}`;
}

function validationTrackSummary(evidence: ShortResearchAsset["validation_evidence"] | null | undefined, replay = false) {
  const horizon = evidenceHorizon(evidence, "5");
  const sampleCount = horizon?.sample_count ?? evidence?.sample_count ?? 0;
  if (!evidence || !sampleCount) {
    return {
      status: replay ? "未运行或样本不足" : "正在积累",
      sampleCount: "0",
      medianReturn: "暂无",
      winRate: "暂无",
      drawdown: "暂无",
      coverage: replay ? "暂无" : "前瞻等待",
      asOfDate: evidence?.freshness?.as_of_date ? formatDate(evidence.freshness.as_of_date) : "暂无"
    };
  }
  return {
    status: validationConfidenceLabel(horizon?.confidence ?? evidence.confidence),
    sampleCount: `${sampleCount}`,
    medianReturn:
      (horizon?.median_return ?? evidence.median_return) === null || (horizon?.median_return ?? evidence.median_return) === undefined
        ? "暂无"
        : formatPercent((horizon?.median_return ?? evidence.median_return ?? 0) * 100),
    winRate:
      (horizon?.win_rate ?? evidence.win_rate) === null || (horizon?.win_rate ?? evidence.win_rate) === undefined
        ? "暂无"
        : formatPercent((horizon?.win_rate ?? evidence.win_rate ?? 0) * 100),
    drawdown:
      horizon?.worst_forward_drawdown === null || horizon?.worst_forward_drawdown === undefined
        ? "暂无"
        : formatPercent(horizon.worst_forward_drawdown * 100),
    coverage:
      horizon?.coverage === null || horizon?.coverage === undefined ? "暂无" : formatPercent(horizon.coverage * 100),
    asOfDate: evidence.freshness?.as_of_date ? formatDate(evidence.freshness.as_of_date) : evidence.as_of_date ? formatDate(evidence.as_of_date) : "暂无"
  };
}

function validationEvidenceText(asset: ShortResearchAsset | null | undefined) {
  const evidence = asset?.validation_evidence;
  const replay = evidenceTrack(asset, "historical_replay");
  const forward = evidenceTrack(asset, "forward_live");
  if (replay || forward) {
    return `${validationTrackText(replay, "历史回放", { replay: true })}；${validationTrackText(forward, "真实前瞻")}`;
  }
  if (!evidence || !evidence.sample_count) {
    return "标签验证：样本不足";
  }
  const winRate = evidence.win_rate === null || evidence.win_rate === undefined ? "暂无" : formatPercent(evidence.win_rate * 100);
  const medianReturn =
    evidence.median_return === null || evidence.median_return === undefined
      ? "暂无"
      : formatPercent(evidence.median_return * 100);
  const freshness = evidence.freshness?.as_of_date ? `，样本日期 ${formatDate(evidence.freshness.as_of_date)}` : "";
  const warning = evidence.degradation_warning ? `；${evidence.degradation_warning}` : "";
  return `标签验证：${validationConfidenceLabel(evidence.confidence)}，样本 ${evidence.sample_count}，5日中位收益 ${medianReturn}，胜率 ${winRate}，${sampleQualityLabel(evidence)}${freshness}${warning}`;
}

function evidenceContractText(status: string | null | undefined, summary?: Record<string, unknown> | null) {
  const sampleCount = typeof summary?.sample_count === "number" ? summary.sample_count : 0;
  switch (status) {
    case "同源已验证":
      return `当前结果已有同源历史证据，样本 ${sampleCount}；它只代表历史复盘，不保证未来。`;
    case "样本不足":
      return `当前规则已有契约记录，但样本只有 ${sampleCount}，还不能下可靠结论。`;
    case "版本不一致":
      return "现有证据来自不同规则版本或不同数据口径，只能参考，不能证明当前标签。";
    case "旧口径结果":
      return "这是旧模拟盘或旧回测口径，不等于当前策略证据。";
    case "等待验证":
    default:
      return "当前标签或组合正在等待同源验证；页面可以观察，但不要把它当成已证明有效。";
  }
}

function recordNumber(metadata: Record<string, unknown> | null | undefined, key: string) {
  const value = metadata?.[key];
  return typeof value === "number" ? value : null;
}

function observationPortfolioText(asset: ShortResearchAsset | null | undefined) {
  const context = asset?.observation_portfolio;
  if (!context || !context.status) {
    return "ETF 资金配置：暂无权重快照";
  }
  if (context.status === "included" || context.status === "defensive" || context.status === "satellite") {
    const label =
      context.status === "defensive" ? "防守仓位" : context.status === "satellite" ? "小仓观察" : "进攻仓位";
    return context.weight_explanation ?? `ETF 资金配置：${label}参考权重 ${formatPercent((context.target_weight ?? 0) * 100)}，仅作研究参考`;
  }
  const reason = context.exclusion_explanation || context.exclusion_reason || context.risk_reasons?.[0] || "未进入主观察组合";
  return `ETF 资金配置：${context.status === "watch_only" ? "只观察不配权" : "未配权"}，${reason}`;
}

function thresholdExplanationLine(position: TrackedPosition | null | undefined) {
  const explanation = position?.dynamic_thresholds?.explanation?.[0];
  if (explanation) {
    return explanation;
  }
  const alertExplanation = position?.latest_alert?.threshold_context?.explanation;
  if (Array.isArray(alertExplanation) && typeof alertExplanation[0] === "string") {
    return alertExplanation[0];
  }
  return "动态线等待足够价格数据后计算。";
}


function signedScore(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "暂无";
  }
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)} 分`;
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

function entryTimingTone(label: string) {
  if (label === "健康回踩" || label === "趋势延续") {
    return "bg-emerald-100 text-emerald-800";
  }
  if (label === "冲高别追" || label === "放量转弱") {
    return "bg-rose-100 text-rose-800";
  }
  if (label === "跌破等待") {
    return "bg-amber-100 text-amber-900";
  }
  return "bg-stone-200 text-stone-700";
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

function isLiveRankingItem(item: RankedAssetItem): item is IntradayEtfLiveRankingItem {
  return "live_rank" in item && "etf_code" in item;
}

function getItemAssetType(item: RankedAssetItem) {
  return isLiveRankingItem(item) ? "etf" : item.asset_type;
}

function toEtfItemCode(item: RankedAssetItem) {
  return isLiveRankingItem(item) ? item.etf_code : item.code;
}

function toEtfItemName(item: RankedAssetItem) {
  return isLiveRankingItem(item) ? item.etf_name : item.name;
}

function toEtfItemRank(item: RankedAssetItem) {
  return isLiveRankingItem(item) ? item.live_scope_rank ?? item.live_rank : item.rank;
}

function etfGlobalRank(item: IntradayEtfLiveRankingItem) {
  return item.base_global_rank ?? item.base_rank;
}

function etfFilteredPosition(item: IntradayEtfLiveRankingItem) {
  return item.filtered_position;
}

function itemConclusion(item: RankedAssetItem) {
  return item.conclusion ?? "数据不足";
}

function etfLiveRankChangeText(rankChange: number | null) {
  if (rankChange === null) {
    return "暂无可比变化";
  }
  if (rankChange === 0) {
    return "持平";
  }
  if (rankChange > 0) {
    return `↑${rankChange}`;
  }
  return `↓${Math.abs(rankChange)}`;
}

function formatLiveScore(value: number | null | undefined) {
  return value === null || value === undefined ? "暂无" : value.toFixed(1);
}

function liveScoreText(item: IntradayEtfLiveRankingItem) {
  if (item.score_source === "intraday") {
    return "实时综合分 " + formatLiveScore(item.live_total_score) + " 分（盘中调整 " + signedScore(item.intraday_adjustment_score) + "）";
  }
  if (item.score_source === "daily") {
    return "日线基础分 " + formatLiveScore(item.base_score ?? item.live_total_score) + " 分";
  }
  return "暂无分数";
}

function formatOptionalScore(value: number | null | undefined) {
  return value === null || value === undefined ? "暂无" : value.toFixed(1);
}

function canonicalAssetScore(asset: ShortResearchAsset | null | undefined) {
  if (!asset) {
    return null;
  }
  const score =
    asset.asset_type === "etf"
      ? asset.score_eligible === true
        ? asset.ranking_score
        : null
      : asset.total_score;
  return typeof score === "number" && Number.isFinite(score) ? score : null;
}

function assetScoreText(asset: ShortResearchAsset | null | undefined) {
  const score = canonicalAssetScore(asset);
  return score === null ? "暂无分数" : `${score.toFixed(1)} 分`;
}

function assetScoreSummaryText(asset: ShortResearchAsset | null | undefined) {
  const score = canonicalAssetScore(asset);
  return `综合分 ${score === null ? "暂无" : score.toFixed(1)}`;
}

function historyTierText(tier: string | null | undefined) {
  switch (tier) {
    case "provisional_short_history":
      return "短样本（61-119 个交易日）";
    case "standard_history":
      return "标准历史（120-249 个交易日）";
    case "full_history_context":
      return "完整历史背景（至少 250 个交易日）";
    default:
      return "历史层级暂无";
  }
}

function actionableStateText(asset: ShortResearchAsset) {
  if (asset.actionable_eligible && asset.actionable_rank !== null && asset.actionable_rank !== undefined) {
    return `可行动榜 #${asset.actionable_rank}`;
  }
  const reasons = asset.actionable_exclusion_reasons ?? [];
  return reasons.length ? `暂不可行动：${reasons.slice(0, 2).join("；")}` : "暂不可行动：缺少行动证据";
}

function opportunityScoreText(asset: ShortResearchAsset | null | undefined) {
  if (!asset?.opportunity_score && asset?.opportunity_score !== 0) {
    return "暂无主题辅助";
  }
  return `主题辅助 ${formatOptionalScore(asset.opportunity_score)} 分`;
}

function opportunityStatusText(asset: ShortResearchAsset | null | undefined) {
  const label = asset?.opportunity_label ?? "暂无主题催化";
  if (!asset?.opportunity_score && asset?.opportunity_score !== 0) {
    return label;
  }
  return `${label} · ${opportunityScoreText(asset)}`;
}

function opportunityVersionText(asset: ShortResearchAsset | null | undefined) {
  switch (asset?.opportunity_score_version) {
    case "etf_factor_profile_v1_full":
      return "口径：ETF 多因子完整口径";
    case "etf_factor_profile_v1_degraded":
      return "口径：ETF 多因子降级口径";
    case "etf_factor_profile_v1_unavailable":
      return "暂无主题辅助口径";
    case "opportunity_score_v2_full":
    case "opportunity_score_v2_sector_only":
    case "opportunity_score_v2_catalyst_only":
    case "opportunity_score_v1":
      return "历史主题辅助口径已停用；催化上下文仅作独立影子研究。";
    default:
      return "暂无主题辅助口径";
  }
}

const factorGroupOrder = ["price_momentum", "sector_trend", "theme_event", "fund_flow", "constituent_breadth", "liquidity", "etf_structure"];

function factorProfileText(asset: ShortResearchAsset | null | undefined) {
  if (!asset?.factor_profile_version) {
    return "因子口径：暂无";
  }
  const score = formatOptionalScore(asset.factor_profile_score);
  if (asset.factor_profile_version === "etf_factor_profile_v1_unavailable") {
    return "因子口径：暂无综合因子";
  }
  const label = asset.factor_profile_version === "etf_factor_profile_v1_full" ? "完整多因子" : "降级多因子";
  return `因子口径：${label} · ${score} 分`;
}

function factorGroupHighlights(asset: ShortResearchAsset | null | undefined) {
  const groups = asset?.factor_group_scores;
  if (!groups) {
    return [];
  }
  return factorGroupOrder
    .map((key) => groups[key])
    .filter(Boolean)
    .slice(0, 6)
    .map((item) => ({
      key: item.group,
      label: item.label,
      value: item.score === null || item.score === undefined ? "暂无" : `${item.score.toFixed(1)} 分`,
      muted: item.availability !== "available"
    }));
}

function activeRiskGateText(asset: ShortResearchAsset | null | undefined) {
  const active = (asset?.risk_gates ?? []).filter((gate) => gate.active === true);
  if (!active.length) {
    return "风险门槛：未触发硬门槛";
  }
  return `风险门槛：${active
    .slice(0, 3)
    .map((gate) => String(gate.label ?? gate.gate_id ?? "风险"))
    .join("、")}`;
}

function sectorTrendText(asset: ShortResearchAsset | null | undefined) {
  if (asset?.sector_trend_score === null || asset?.sector_trend_score === undefined) {
    return asset?.sector_trend_reason || "暂无板块趋势数据";
  }
  const label = asset.sector_trend_label || "板块趋势";
  const peers = asset.sector_peer_count ? ` · ${asset.sector_peer_count}只样本` : "";
  return `${label} ${formatOptionalScore(asset.sector_trend_score)} 分${peers}`;
}

function catalystSummaryText(asset: ShortResearchAsset | null | undefined) {
  if (asset?.asset_type !== "etf") {
    return asset?.catalyst_summary || "暂无主题催化数据，先按技术结构观察。";
  }
  switch (asset.catalyst_shadow?.coverage_state) {
    case "active":
      return "有已核验、在当前截止时点有效的催化事实；仅作影子研究。";
    case "observed_none":
      return "官方来源已观测，本期没有适用的催化事件。";
    case "not_applicable":
      return "已登记来源不适用于当前主题。";
    case "unavailable":
    default:
      return "催化影子证据暂不可用，不按中性或无事件处理。";
  }
}

function catalystDirectionText(direction: string) {
  const labels: Record<string, string> = {
    positive: "正向",
    negative: "负向",
    mixed: "混合",
    neutral: "中性"
  };
  return labels[direction] ?? direction;
}

function CatalystShadowPanel({ asset }: { asset: ShortResearchAsset }) {
  const shadow = asset.catalyst_shadow;
  if (!shadow) {
    return (
      <div className="rounded-[10px] border border-dashed border-ink/20 bg-paper p-4">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">催化影子研究</p>
        <p className="mt-2 text-sm leading-6 text-ink/60">尚无已完成的缓存影子快照。</p>
      </div>
    );
  }
  const events = shadow.themes.flatMap((theme) =>
    theme.events.map((event) => {
      const receipt = theme.receipts.find((item) => event.receipt_ids.includes(item.receipt_id));
      return { event, receipt, themeId: theme.snapshot.theme_id };
    })
  );
  const directEvents = events.filter(({ event }) => event.mapping_kind === "direct");
  const proxyEvents = events.filter(({ event }) => event.mapping_kind === "proxy");
  return (
    <div
      data-catalyst-shadow
      className="rounded-[10px] border border-dashed border-accent/35 bg-paper p-4"
    >
      <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">催化影子研究</p>
        <p className="text-xs text-ink/50">覆盖状态：{shadow.coverage_state}</p>
      </div>
      <p className="mt-2 text-sm leading-6 text-ink/65">{catalystSummaryText(asset)}</p>
      {directEvents.length ? (
        <div className="mt-3 space-y-2">
          {directEvents.map(({ event, receipt, themeId }) => (
            <article key={`${event.event_id}:${event.event_version}:${themeId}`} className="rounded-[8px] border border-ink/10 bg-white p-3">
              <p className="text-sm font-semibold text-ink">
                {event.title}
                {event.is_correction ? <span className="ml-2 text-xs text-accent">更正版</span> : null}
              </p>
              <p className="mt-1 text-xs text-ink/50">
                直接映射 · {themeId} · {catalystDirectionText(event.direction)} · 发布 {formatDate(event.source_published_at)}
                {" · "}首次收到 {formatDate(event.first_received_at)}
              </p>
              <p className="mt-2 text-sm leading-6 text-ink/65">{event.summary}</p>
              <p className="mt-1 text-xs text-ink/50">
                有效期 {formatDate(event.effective_start)} 至 {formatDate(event.effective_end)}
                {receipt?.canonical_url ? (
                  <>
                    {" · "}
                    <a className="text-accent underline" href={receipt.canonical_url} target="_blank" rel="noreferrer">
                      官方来源
                    </a>
                  </>
                ) : null}
              </p>
            </article>
          ))}
        </div>
      ) : null}
      {proxyEvents.length ? (
        <p className="mt-3 text-xs leading-5 text-ink/55">
          代理映射（仅展示）：{proxyEvents.map(({ event, themeId }) => `${event.title} → ${themeId}`).join("；")}
        </p>
      ) : null}
      {shadow.limitations.length ? (
        <p className="mt-3 text-xs leading-5 text-ink/50">限制：{shadow.limitations.join("；")}</p>
      ) : null}
      <p className="mt-3 border-t border-ink/10 pt-3 text-xs font-medium leading-5 text-ink/60">
        影子研究，权重为 0；不改变研究榜、可行动榜、组合配置、持仓动作或邮件状态。
      </p>
    </div>
  );
}

const v3ScoreDimensionLabels: Array<{ key: string; label: string }> = [
  { key: "technical_momentum_cross_section", label: "技术动量" },
  { key: "risk_quality_cross_section", label: "风险质量" },
  { key: "structure_liquidity", label: "结构/流动性" },
  { key: "sector_trend", label: "板块趋势" },
  { key: "theme_catalyst", label: "主题催化" },
  { key: "premium_discount", label: "折溢价" }
];

const legacyScoreDimensionLabels: Array<{ key: string; label: string }> = [
  { key: "cross_sectional_percentile", label: "横截面分位" },
  { key: "dynamic_threshold", label: "动态阈值" },
  { key: "label_evidence", label: "历史有效性" },
  { key: "data_reliability", label: "数据可信度" },
  { key: "liquidity_premium", label: "流动性/折溢价" }
];

function finalScoreBreakdown(asset: ShortResearchAsset | null | undefined) {
  const v3 = asset?.score_breakdown?.final_score_v3;
  if (v3 && typeof v3 === "object") {
    return v3;
  }
  const v2 = asset?.score_breakdown?.final_score_v2;
  return v2 && typeof v2 === "object" ? v2 : null;
}

function finalScoreVersion(asset: ShortResearchAsset | null | undefined) {
  if (
    asset?.score_breakdown?.final_score_v3 &&
    typeof asset.score_breakdown.final_score_v3 === "object"
  ) {
    return "final_score_v3";
  }
  if (
    asset?.score_breakdown?.final_score_v2 &&
    typeof asset.score_breakdown.final_score_v2 === "object"
  ) {
    return "final_score_v2";
  }
  return null;
}

function scoreVersionText(asset: ShortResearchAsset | null | undefined) {
  const final = finalScoreBreakdown(asset);
  const version =
    finalScoreVersion(asset) ??
    final?.score_version ??
    asset?.metrics?.score_version ??
    asset?.score_breakdown?.score_version;
  if (version === "final_score_v3") {
    return "当前评分口径";
  }
  return "旧口径结果";
}

function scoreDimensionLabels(asset: ShortResearchAsset | null | undefined) {
  return finalScoreVersion(asset) === "final_score_v3"
    ? v3ScoreDimensionLabels
    : legacyScoreDimensionLabels;
}

function scoreDimensionValue(asset: ShortResearchAsset | null | undefined, key: string) {
  const final = finalScoreBreakdown(asset);
  const score = final?.component_scores?.[key] ?? final?.components?.[key]?.score;
  return typeof score === "number" && Number.isFinite(score)
    ? score.toFixed(1)
    : "暂无";
}

function hasFreshIntradayChange(
  quote: IntradayEtfLiveRankingItem["quote"],
  marketStatus?: string | null,
): quote is FreshIntradayQuote {
  return Boolean(
    quote &&
      !quote.is_stale &&
      quote.decision_eligible &&
      (!marketStatus || marketStatus === "open") &&
      typeof quote.change_percent === "number"
  );
}

function intradayChangeDisplay(
  quote: IntradayEtfLiveRankingItem["quote"],
  marketStatus?: string | null,
) {
  if (!quote) {
    if (marketStatus === "lunch_break") {
      return { value: "午休，等待下午开盘", time: "暂无行情时间" };
    }
    if (marketStatus && marketStatus !== "open") {
      return { value: "休市，暂无最近行情", time: "暂无行情时间" };
    }
    return { value: "等待盘中行情", time: "暂无行情时间" };
  }
  const changeValue =
    quote.change_percent === null || quote.change_percent === undefined
      ? null
      : formatPercent(quote.change_percent);
  if (marketStatus && marketStatus !== "open") {
    const suffix = marketStatus === "lunch_break" ? "午休最近行情" : "最近行情";
    return { value: changeValue ? `${changeValue}（${suffix}）` : suffix, time: formatDateTime(quote.quote_time) };
  }
  if (!quote.decision_eligible) {
    return { value: changeValue ? `${changeValue}（仅网页参考）` : "仅网页参考", time: formatDateTime(quote.quote_time) };
  }
  if (quote.is_stale) {
    return { value: changeValue ? `${changeValue}（行情滞后）` : "行情滞后", time: formatDateTime(quote.quote_time) };
  }
  if (!changeValue) {
    return { value: "等待新鲜盘中行情", time: formatDateTime(quote.quote_time) };
  }
  return { value: changeValue, time: formatDateTime(quote.quote_time) };
}
function isLiveRankingResponse(value: RankedAssetResponse | undefined): value is IntradayEtfLiveRankingList {
  return Boolean(value && "signal_as_of_date" in value && "latest_run" in value);
}

function liveRankingQuote(item: RankedAssetItem) {
  return isLiveRankingItem(item) ? item.quote : null;
}

function dailyReferenceParts(reason: string | null | undefined, asOfDate: string | null | undefined) {
  const normalized = (reason?.trim() || "暂无日线参考原因").replaceAll("今天", "该日");
  const readable = asOfDate ? normalized : normalized.replaceAll("该日", "最近日线");
  const clauses = readable
    .split(/[，。；]/)
    .map((item) => item.trim())
    .filter(Boolean);
  const parts: Array<{ label: string; value: string }> = [
    { label: "参考日", value: asOfDate ? formatDate(asOfDate) : "最近日线" },
  ];
  for (const clause of clauses) {
    if (clause.includes("近5/20/60") || clause.includes("近 5 / 20 / 60")) {
      parts.push({ label: "趋势", value: clause });
    } else if (clause.includes("该日") || clause.includes("最近日线")) {
      parts.push({ label: "当日涨跌", value: clause });
    } else if (clause.includes("10日线") || clause.includes("均线")) {
      parts.push({ label: "均线位置", value: clause });
    } else if (clause.includes("趋势") || clause.includes("破坏")) {
      parts.push({ label: "结论", value: clause });
    } else {
      parts.push({ label: "补充", value: clause });
    }
  }
  if (!parts.some((part) => part.label === "结论")) {
    parts.push({ label: "结论", value: "日线只作背景，需结合最新盘中走势。" });
  }
  return parts;
}

function dailyReferenceReason(reason: string | null | undefined, asOfDate: string | null | undefined) {
  return dailyReferenceParts(reason, asOfDate)
    .map((part) => `${part.label}：${part.value}`)
    .join("；");
}
function itemEntryTimingDisplay(item: RankedAssetItem, marketStatus?: string | null) {
  if (!isLiveRankingItem(item)) {
    return {
      title: "今日买点",
      label: item.entry_timing_label,
      reason: rationaleText(item, "key_reason", "按近期趋势、风险和数据质量生成。"),
    };
  }
  const hasFreshLiveTiming =
    item.score_source === "intraday" &&
    marketStatus === "open" &&
    item.quote !== null &&
    item.quote.decision_eligible &&
    !item.quote.is_stale &&
    item.live_entry_timing_label !== "数据不足";
  if (hasFreshLiveTiming) {
    return {
      title: "盘中买点状态",
      label: item.live_entry_timing_label,
      reason: item.live_entry_timing_reason,
    };
  }
  if (marketStatus !== "open") {
    const isLunchBreak = marketStatus === "lunch_break";
    return {
      title: "盘中买点状态",
      label: isLunchBreak ? "午休" : "休市",
      reason: isLunchBreak
        ? "当前是午休时段，不产生新的盘中买点；页面展示上午最近行情，下午开盘后继续刷新。"
        : "当前非交易时段，不输出盘中买点；休市后盘中行情正常不再刷新，只展示最近公开行情和日线买点参考。",
    };
  }
  if (item.quote?.is_stale) {
    return {
      title: "盘中买点状态",
      label: "行情滞后",
      reason: "盘中行情已滞后，等待新鲜盘中行情；日线买点只能作为参考。",
    };
  }
  return {
    title: "盘中买点状态",
    label: "数据不足",
    reason: "等待新鲜盘中行情；日线买点只能作为参考。",
  };
}

function shortResearchThemeTags(item: RankedAssetItem): string[] {
  return isLiveRankingItem(item) ? [] : item.theme_tags;
}

function previewAssetFromRankedItem(item: RankedAssetItem, marketStatus?: string | null): ShortResearchAsset {
  if (!isLiveRankingItem(item)) {
    return item;
  }

  const entryTiming = itemEntryTimingDisplay(item, marketStatus);
  const conclusion = itemConclusion(item);
  const keyReason =
    item.score_contribution_reasons[0] ??
    entryTiming.reason ??
    "正在加载完整详情，先使用榜单数据展示核心摘要。";
  const quote = item.quote;
  const metrics: Record<string, unknown> = {};
  if (hasFreshIntradayChange(quote, marketStatus)) {
    metrics.today_return_pct = quote.change_percent / 100;
  }
  if (quote?.turnover !== null && quote?.turnover !== undefined) {
    metrics.average_turnover_20d = quote.turnover;
  }
  const rankingScore =
    item.score_version === "final_score_v3" &&
    item.base_score !== null &&
    Number.isFinite(item.base_score)
      ? item.base_score
      : null;

  return {
    asset_type: "etf",
    code: item.etf_code,
    name: item.etf_name ?? item.etf_code,
    rank: item.live_rank ?? item.base_rank,
    ranking_score: rankingScore,
    score_eligible: rankingScore !== null,
    total_score: item.live_total_score ?? item.base_score ?? null,
    conclusion,
    entry_timing_label: entryTiming.label,
    entry_timing_reason: entryTiming.reason,
    theme_tags: [],
    investment_direction: item.sources.length ? item.sources.join(" / ") : "场内 ETF",
    trading_rule_label: "场内 ETF",
    latest_date: quote?.trade_date ?? null,
    latest_value: quote?.latest_price ?? null,
    usable_days: 0,
    sample_level: "正在加载完整日线详情。",
    metrics,
    score_breakdown: item.score_breakdown ?? {},
    risk_flags: quote?.is_stale && marketStatus === "open" ? ["行情滞后"] : [],
    rationale: {
      key_reason: keyReason,
      label_meaning: labelMeaning(conclusion)
    },
    source_note: item.score_source === "intraday" ? "盘中榜单预览，完整详情加载中" : "日线榜单预览，完整详情加载中",
    advisor_report: null,
    validation_evidence: {
      sample_count: 0,
      confidence: "insufficient",
      sample_quality: null,
      freshness: quote?.trade_date ? { as_of_date: quote.trade_date } : null
    },
    observation_portfolio: {
      status: "watch_only",
      exclusion_reason: "完整详情加载中",
      exclusion_explanation: "正在读取完整详情，暂不显示组合权重。"
    },
    research_signal_contract: {},
    evidence_status: "等待验证",
    evidence_summary: {
      evidence_status: "等待验证",
      sample_count: 0,
      caveats: ["盘中榜单预览等待完整详情和同源历史验证。"]
    }
  };
}

function assetTypeLabel(assetType: string) {
  return assetType === "etf" ? "场内 ETF" : "支付宝场外基金";
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
      return "暂无标签说明。";
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
  const parts = shanghaiClockParts(Date.now());
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function defaultOrderTimeBucket(): OrderTimeBucket {
  return Number(shanghaiClockParts(Date.now()).hour) >= 15 ? "after_15" : "before_15";
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

function positiveNumberOrUndefined(value: string, label: string) {
  const trimmed = value.trim();
  if (!trimmed) {
    return undefined;
  }
  const parsed = Number(trimmed);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    throw new Error(`${label}必须是大于 0 的数字`);
  }
  return parsed;
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
  if (alertType === "confirmed_trend_weakening") {
    return "确认趋势转弱提醒";
  }
  if (alertType === "trend_weakening") {
    return "趋势警戒";
  }
  if (alertType === "hard_stop") {
    return "止损提醒";
  }
  return alertType;
}

const EXIT_ALERT_TYPES = new Set(["exit_watch", "take_profit_watch", "trailing_take_profit", "confirmed_trend_weakening", "hard_stop"]);

function isEmailExitAlert(alertType: string) {
  return EXIT_ALERT_TYPES.has(alertType);
}

function alertDeliveryLabel(alert: {
  alert_type: string;
  email_status: string;
  suppression_status: string | null;
  delivery_status?: string | null;
  quote_time?: string | null;
}) {
  if (
    alert.suppression_status === "web_only" ||
    !isEmailExitAlert(alert.alert_type)
  ) {
    return "仅网页提示";
  }
  if (alert.suppression_status === "suppressed") {
    return "已去重";
  }
  const status = alert.delivery_status ?? alert.email_status;
  if (status === "smtp_accepted" || status === "sent") {
    return "SMTP 已接受";
  }
  if (status === "failed") {
    return "发送失败";
  }
  if (status === "unknown") {
    return "状态未知";
  }
  if (alert.quote_time === null || alert.quote_time === undefined) {
    return "等待数据";
  }
  return "仅网页提示";
}

function actionStatusLabel(status: TrackedPositionActionSummary["status"]) {
  const labels: Record<TrackedPositionActionSummary["status"], string> = {
    proposed: "建议待确认",
    acknowledged: "已确认，待执行",
    partially_executed: "部分执行",
    executed: "已执行",
    expired: "已过期",
    cancelled: "已取消",
    superseded: "已被更严格建议替代"
  };
  return labels[status];
}

function alertStateLabel(state: string | null | undefined) {
  const labels: Record<string, string> = {
    normal: "正常",
    pending: "等待确认",
    firing: "风险持续",
    recovering: "恢复观察",
    resolved: "已解除"
  };
  return state ? (labels[state] ?? state) : "暂无";
}

function dataStateLabel(state: string | null | undefined) {
  const labels: Record<string, string> = {
    eligible: "数据可决策",
    decision_eligible: "数据可决策",
    data_waiting: "等待数据",
    no_data: "等待数据",
    error: "数据异常",
    unknown: "等待数据"
  };
  return state ? (labels[state] ?? state) : "暂无";
}

function trackedLifecycleLabel(position: TrackedPosition) {
  const dataState =
    position.lifecycle_state?.data_state ?? position.current_action?.data_state;
  if (
    ["data_waiting", "no_data", "error", "unknown"].includes(dataState ?? "") ||
    position.exit_signal.action_class === "data_waiting"
  ) {
    return "等待数据";
  }
  if (position.exit_signal.action_class === "soft_watch") {
    return "仅观察";
  }
  return position.current_action
    ? actionStatusLabel(position.current_action.status)
    : "仅观察";
}

function notificationDeliveryLabel(status: string | null | undefined) {
  if (status === "smtp_accepted" || status === "sent") {
    return "SMTP 已接受";
  }
  if (status === "failed") {
    return "发送失败";
  }
  if (status === "unknown") {
    return "状态未知";
  }
  if (status === "pending" || status === "claimed") {
    return "等待发送";
  }
  return status ?? "未产生邮件";
}

function stableActionRequestKey(
  keys: Map<string, string>,
  requestIdentity: string
) {
  const existing = keys.get(requestIdentity);
  if (existing) {
    return existing;
  }
  const key =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `action-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  keys.set(requestIdentity, key);
  return key;
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

function reliabilityLabel(value: string | null | undefined) {
  if (value === "fresh_consensus") {
    return "多源新鲜行情";
  }
  if (value === "single_fresh" || value === "fresh_intraday") {
    return "单源新鲜行情";
  }
  if (value === "daily_close" || value === "stale" || value === "stale_quote") {
    return "非实时参考";
  }
  if (value === "diverged") {
    return "多源分歧";
  }
  if (value === "estimated") {
    return "估算展示";
  }
  if (value === "missing" || value === "unavailable") {
    return "等待行情";
  }
  return "数据口径未知";
}

function quoteSourceName(value: string | null | undefined) {
  if (value === "akshare") {
    return "AKShare";
  }
  if (value === "eastmoney") {
    return "东方财富";
  }
  if (value) {
    return value;
  }
  return "暂无";
}

function consensusLabel(value: string | null | undefined) {
  if (value === "consistent") {
    return "多源一致";
  }
  if (value === "single_provider") {
    return "单源可用";
  }
  if (value === "diverged") {
    return "多源分歧";
  }
  if (value === "stale") {
    return "行情滞后";
  }
  if (value === "unavailable") {
    return "行情不可用";
  }
  return "校验未知";
}

function quoteReliabilityLine(
  quote: IntradayEtfLiveRankingItem["quote"] | null | undefined,
) {
  if (!quote) {
    return "行情来源：暂无；校验状态：等待行情。";
  }
  const providerText = quote.provider_count > 1 ? `${quote.fresh_provider_count}/${quote.provider_count} 源新鲜` : `${quote.provider_count} 源`;
  const decisionText = quote.decision_eligible ? "可用于盘中判断" : "仅网页参考";
  const reason = quote.limitation_reason ? `；${quote.limitation_reason}` : "";
  return `行情来源：${quoteSourceName(quote.source)}；校验状态：${consensusLabel(quote.consensus_status)}（${providerText}，${decisionText}）${reason}`;
}

function snapshotReliabilityLine(snapshot: TrackedPosition["intraday_snapshot"] | null | undefined) {
  if (!snapshot) {
    return "行情来源：暂无；校验状态：等待行情。";
  }
  const providerText = snapshot.provider_count > 1 ? `${snapshot.fresh_provider_count}/${snapshot.provider_count} 源新鲜` : `${snapshot.provider_count} 源`;
  const decisionText = snapshot.decision_eligible ? "可用于盘中提醒" : "仅网页参考";
  const reason = snapshot.limitation_reason ? `；${snapshot.limitation_reason}` : "";
  return `行情来源：${quoteSourceName(snapshot.source)}；校验状态：${consensusLabel(snapshot.consensus_status)}（${providerText}，${decisionText}）${reason}`;
}

function advisorSourceLabel(source: string | null | undefined) {
  if (source === "llm") {
    return "AI生成";
  }
  if (source === "partial_fallback") {
    return "规则补齐（AI 输出不完整）";
  }
  if (source === "fallback") {
    return "规则说明";
  }
  return "规则解释";
}

function marketStatusLabel(value: string | undefined) {
  if (value === "open") {
    return "交易中";
  }
  if (value === "lunch_break") {
    return "午休";
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

function shanghaiClockParts(timestamp: number) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    weekday: "short",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23"
  }).formatToParts(new Date(timestamp));
  const value = (type: string) => parts.find((part) => part.type === type)?.value ?? "";
  return {
    weekday: value("weekday"),
    year: value("year"),
    month: value("month"),
    day: value("day"),
    hour: value("hour"),
    minute: value("minute")
  };
}

function AssetPaginationBar({
  total,
  offset,
  visibleCount,
  isFetching,
  scopeLabel,
  onPrevious,
  onNext
}: {
  total: number;
  offset: number;
  visibleCount: number;
  isFetching: boolean;
  scopeLabel: string;
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
    <div className="flex flex-col gap-3 rounded-[8px] border border-border bg-white px-3 py-2 text-sm text-ink/65 md:flex-row md:items-center md:justify-between">
      <span>
        {scopeLabel}：当前页 {start} - {end} / {total} 只
      </span>
      <div className="flex gap-2">
        <button
          className="rounded-[6px] border border-border bg-white px-3 py-2 font-medium text-ink disabled:opacity-40"
          disabled={!canPrevious || isFetching}
          onClick={onPrevious}
        >
          上一页
        </button>
        <button
          className="rounded-[6px] bg-ink px-3 py-2 font-medium text-white disabled:opacity-40"
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
      className={`rounded-[6px] px-3 py-2 text-sm font-medium transition disabled:opacity-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent ${className}`}
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
    <div className={`rounded-[8px] border border-border px-3 py-2.5 ${tone}`}>
      <p className="text-[11px] font-medium uppercase tracking-[0.14em] opacity-60">{label}</p>
      <p className="mt-1 font-mono text-base font-semibold leading-tight">{value}</p>
    </div>
  );
}

function DetailLoadingPlaceholder({ text }: { text: string }) {
  return (
    <div className="flex h-full items-center justify-center rounded-[10px] bg-white text-sm text-ink/50 transition-opacity duration-150">
      {text}
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

function ShortTermClient() {
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const searchParams = useSearchParams();
  const requestedSection = searchParams.get("section");
  const holdingsSectionRef = useRef<HTMLDivElement | null>(null);
  const summarySectionRef = useRef<HTMLDivElement | null>(null);
  const chartsSectionRef = useRef<HTMLDivElement | null>(null);
  const advisorSectionRef = useRef<HTMLDivElement | null>(null);
  const explanationSectionRef = useRef<HTMLDivElement | null>(null);
  const trackingSectionRef = useRef<HTMLDivElement | null>(null);
  const [assetType, setAssetType] = useState<AssetType>("etf");
  const [rankingSurface, setRankingSurface] = useState<RankingSurface>("research");
  const [theme, setTheme] = useState("all");
  const [sort, setSort] = useState<SortKey>("opportunity");
  const [keyword, setKeyword] = useState("");
  const [labelFilters, setLabelFilters] = useState<LabelFilterState>(emptyLabelFilters);
  const [labelFilterExpanded, setLabelFilterExpanded] = useState(false);
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
  const [editingTrackingId, setEditingTrackingId] = useState<number | null>(null);
  const [editBuyAmount, setEditBuyAmount] = useState("");
  const [editBuyDate, setEditBuyDate] = useState("");
  const [editOrderTime, setEditOrderTime] = useState<OrderTimeBucket>("before_15");
  const [editConfirmedNavDate, setEditConfirmedNavDate] = useState("");
  const [editConfirmedNav, setEditConfirmedNav] = useState("");
  const [editConfirmedShares, setEditConfirmedShares] = useState("");
  const [editNote, setEditNote] = useState("");
  const [actionExecutionDrafts, setActionExecutionDrafts] = useState<
    Record<number, ActionExecutionDraft>
  >({});
  const [actionNotice, setActionNotice] = useState<{
    actionId: number;
    text: string;
  } | null>(null);
  const actionRequestKeys = useRef(new Map<string, string>());
  const actionRequestPayloads = useRef(
    new Map<string, TrackedPositionActionTransitionRequest>()
  );
  const [pendingDesktopScrollKey, setPendingDesktopScrollKey] = useState<string | null>(null);
  const mode = assetModes[assetType];
  const sortOptions = assetType === "etf" ? etfSortOptions : baseSortOptions;
  const labelFilterSignature = useMemo(() => JSON.stringify(labelFilters), [labelFilters]);

  const status = useQuery({
    queryKey: ["short-research", "status"],
    queryFn: async ({ signal }) =>
      (await api.get<ShortResearchStatus>("/api/short-research/status?include_health=true", { signal })).data
  });

  const latestCompletedSignal = useQuery({
    queryKey: ["short-research", "signals", "latest", assetType, theme],
    queryFn: async ({ signal }) => {
      const params = new URLSearchParams({ asset_type: assetType });
      if (theme !== "all") {
        params.set("theme", theme);
      }
      return (await api.get<ShortResearchSignalRun | null>(`/api/short-research/signals/latest?${params.toString()}`, { signal })).data;
    }
  });

  const assets = useQuery({
    queryKey: ["short-research", "assets", assetType, rankingSurface, theme, sort, keyword, assetOffset, labelFilterSignature],
    queryFn: async ({ signal }) => {
      const params = new URLSearchParams();
      params.set("asset_type", assetType);
      params.set("limit", String(ASSET_PAGE_SIZE));
      params.set("offset", String(assetOffset));
      appendLabelFilterParams(params, labelFilters);
      if (theme !== "all") {
        params.set("theme", theme);
      }
      if (keyword.trim()) {
        params.set("q", keyword.trim());
      }
      params.set("sort", sort);
      if (assetType === "etf") {
        params.set("ranking_surface", rankingSurface);
      }
      return (await api.get<ShortResearchAssetList>(`/api/short-research/assets?${params.toString()}`, { signal })).data;
    },
    refetchInterval: false
  });

  const etfLiveStatusQuery = useQuery({
    queryKey: ["etf-quotes", "live-ranking-status"],
    enabled: assetType === "etf",
    queryFn: async ({ signal }) =>
      (await api.get<IntradayEtfLiveRankingList>("/api/etf-quotes/live-rankings?limit=1", { signal })).data,
    refetchInterval: (query) => {
      const data = query.state.data as IntradayEtfLiveRankingList | undefined;
      const seconds = data?.market_status === "open" ? data.page_poll_seconds : data?.next_poll_seconds;
      return seconds && seconds > 0 ? seconds * 1_000 : false;
    }
  });

  const etfLiveData = isLiveRankingResponse(assets.data) ? assets.data : null;
  const effectiveEtfLiveStatus = etfLiveData ?? etfLiveStatusQuery.data ?? null;
  const isEtfLiveRanking = etfLiveData !== null && assetType === "etf";
  const shortAssetData = isEtfLiveRanking ? null : (assets.data as ShortResearchAssetList | undefined);
  const etfThemeSource = useQuery({
    queryKey: ["short-research", "etf-theme-heat"],
    enabled: assetType === "etf",
    queryFn: async () =>
      (
        await api.get<ShortResearchAssetList>(
          "/api/short-research/assets?asset_type=etf&ranking_surface=research&universe=all&sort=score&limit=1&offset=0"
        )
      ).data,
    staleTime: 5 * 60_000
  });
  const shouldRefreshIntradayQueries = assetType === "etf" && effectiveEtfLiveStatus?.market_status === "open";
  const selectedEtfLiveData = useQuery({
    queryKey: ["etf-quotes", "selected-live-ranking", selected?.code],
    enabled: assetType === "etf" && selected?.asset_type === "etf" && Boolean(selected?.code),
    queryFn: async ({ signal }) => {
      const params = new URLSearchParams();
      params.set("limit", "5");
      params.set("q", selected?.code ?? "");
      return (await api.get<IntradayEtfLiveRankingList>(`/api/etf-quotes/live-rankings?${params.toString()}`, { signal })).data;
    },
    refetchInterval: (query) => {
      const data = query.state.data as IntradayEtfLiveRankingList | undefined;
      const seconds = data?.market_status === "open" ? data.page_poll_seconds : data?.next_poll_seconds;
      return seconds && seconds > 0 ? seconds * 1_000 : false;
    }
  });

  const observationPortfolio = useQuery({
    queryKey: ["short-research", "observation-portfolio", "default"],
    enabled: assetType === "etf" && Boolean(assets.data?.items.length),
    queryFn: async () =>
      (
        await api.get<ShortResearchObservationPortfolio>(
          "/api/short-research/observation-portfolio?asset_type=etf&universe=default"
        )
      ).data
  });

  const etfOptimizedAllocation = useQuery({
    queryKey: ["short-research", "etf-optimized-allocation", "latest"],
    enabled: assetType === "etf",
    queryFn: async () =>
      (await api.get<EtfOptimizedAllocation | null>("/api/short-research/etf-optimized-allocation/latest")).data
  });

  const selectedDetail = useQuery({
    queryKey: ["short-research", "detail", selected?.asset_type, selected?.code],
    enabled: selected !== null,
    queryFn: async ({ signal }) =>
      (
        await api.get<ShortResearchAssetDetail>(
          `/api/short-research/assets/${selected?.asset_type}/${selected?.code}`,
          { signal }
        )
      ).data
  });

  const trackedPositions = useQuery({
    queryKey: ["tracked-positions", user?.id ?? "anonymous"],
    enabled: Boolean(user),
    queryFn: async ({ signal }) => (await api.get<TrackedPositionList>("/api/tracked-positions", { signal })).data,
    refetchInterval: shouldRefreshIntradayQueries ? (etfLiveData?.page_poll_seconds ?? 30) * 1_000 : false
  });

  const rawAssets = useMemo(() => (assets.data?.items ?? []) as RankedAssetItem[], [assets.data?.items]);
  const activeTracked = useMemo(
    () => (trackedPositions.data?.items ?? []).filter((item) => item.status === "active"),
    [trackedPositions.data?.items],
  );
  const trackedByAsset = useMemo(() => {
    const map = new Map<string, TrackedPosition>();
    for (const item of activeTracked) {
      map.set(`${item.asset_type}-${item.asset_code}`, item);
    }
    return map;
  }, [activeTracked]);
  const activeLabelFilterCount = labelFilters.observation.length + labelFilters.entry.length + labelFilters.tracking.length;
  const assetCountScope =
    theme !== "all" || Boolean(keyword.trim()) || activeLabelFilterCount > 0 ? "当前筛选结果" : "当前完整范围";
  const showLabelFilterDetails = labelFilterExpanded || activeLabelFilterCount > 0;
  const selectedLabelChips = useMemo(
    () =>
      labelFilterGroups.flatMap((group) =>
        labelFilters[group.key].map((value) => ({
          groupKey: group.key,
          groupTitle: group.title,
          value,
        })),
      ),
    [labelFilters],
  );
  const visibleAssets = rawAssets;

  useEffect(() => {
    return () => {
      void queryClient.cancelQueries({ queryKey: ["tracked-positions", user?.id ?? "anonymous"] });
      queryClient.removeQueries({ queryKey: ["tracked-positions", user?.id ?? "anonymous"] });
      void queryClient.cancelQueries({ queryKey: ["tracked-position", user?.id ?? "anonymous"] });
      queryClient.removeQueries({ queryKey: ["tracked-position", user?.id ?? "anonymous"] });
      void queryClient.cancelQueries({ queryKey: ["tracked-position-audit", user?.id ?? "anonymous"] });
      queryClient.removeQueries({ queryKey: ["tracked-position-audit", user?.id ?? "anonymous"] });
    };
  }, [queryClient, user?.id]);

  useEffect(() => {
    setAssetOffset(0);
  }, [assetType, theme, sort, keyword, labelFilterSignature]);

  useEffect(() => {
    const first = visibleAssets[0];
    if (!first) {
      setSelected(null);
      return;
    }
    if (
      !selected ||
      !visibleAssets.some((item) => getItemAssetType(item) === selected.asset_type && toEtfItemCode(item) === selected.code)
    ) {
      setSelected({ asset_type: getItemAssetType(first), code: toEtfItemCode(first) });
    }
  }, [selected, visibleAssets]);

  useEffect(() => {
    if (assetType === "fund" && !baseSortOptions.some((option) => option.key === sort)) {
      setSort("score");
    }
  }, [assetType, sort]);

  const themes = useMemo(() => {
    if (assetType === "etf") {
      const seen = new Set<string>(fallbackThemes);
      for (const item of etfThemeSource.data?.theme_heat ?? []) {
        seen.add(item.theme);
      }
      return ["all", ...Array.from(seen).sort((left, right) => left.localeCompare(right, "zh-CN"))];
    }
    const seen = new Set<string>(fallbackThemes);
    for (const item of assets.data?.items ?? []) {
      for (const tag of (item as ShortResearchAsset).theme_tags) {
        seen.add(tag);
      }
    }
    return ["all", ...Array.from(seen).sort((left, right) => left.localeCompare(right, "zh-CN"))];
  }, [assetType, assets.data, etfThemeSource.data]);

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
    mutationFn: async () => {
      if (assetType === "etf") {
        return { kind: "canonical_refresh" as const };
      }
      const run = (
        await api.post<ShortResearchSignalRun>("/api/short-research/signals/run", {
          asset_type: assetType,
          theme: theme === "all" ? null : theme
        })
      ).data;
      return { kind: "fund_signal" as const, run };
    },
    onSuccess: async (result) => {
      if (result.kind === "canonical_refresh") {
        setSort("opportunity");
        setAssetOffset(0);
        setLastResult({
          status: "refreshed",
          message: "已刷新 canonical v3 综合排名状态；未触发 legacy ETF 信号。"
        });
      } else {
        setLastResult({
          as_of_date: result.run.as_of_date,
          item_count: result.run.summary.item_count,
          conclusion_counts: result.run.summary.conclusion_counts
        });
      }
      await queryClient.invalidateQueries({ queryKey: ["short-research"] });
    }
  });

  const runEtfOptimizedAllocation = useMutation({
    mutationFn: async () =>
      (await api.post<EtfOptimizedAllocation>("/api/short-research/etf-optimized-allocation/run")).data,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["short-research", "etf-optimized-allocation"] }),
        queryClient.invalidateQueries({ queryKey: ["short-research", "observation-portfolio"] })
      ]);
    }
  });

  const optimizedAllocationData = observationPortfolio.data?.optimized_allocation ?? etfOptimizedAllocation.data ?? null;
  const advisorSourceRunId =
    assetType === "etf"
      ? (etfLiveData?.snapshot?.snapshot_id ?? shortAssetData?.snapshot?.snapshot_id ?? null)
      : (latestCompletedSignal.data?.id ?? null);

  const runAdvisor = useMutation({
    mutationFn: async () => {
      if (advisorSourceRunId === null) {
        throw new Error("等待已完成的研究快照后才能生成顾问说明。");
      }
      return (
        await api.post<Record<string, unknown>>("/api/short-research/advisor/run", {
          asset_type: assetType,
          theme: theme === "all" ? null : theme,
          source_signal_run_id: advisorSourceRunId
        })
      ).data;
    },
    onSuccess: async (result) => {
      setLastResult(result);
      await queryClient.invalidateQueries({ queryKey: ["short-research"] });
    }
  });
  const isResearchTaskPending =
    syncData.isPending || runSignals.isPending || runAdvisor.isPending || runEtfOptimizedAllocation.isPending;

  const resetEditTracking = () => {
    setEditingTrackingId(null);
    setEditBuyAmount("");
    setEditBuyDate("");
    setEditOrderTime("before_15");
    setEditConfirmedNavDate("");
    setEditConfirmedNav("");
    setEditConfirmedShares("");
    setEditNote("");
  };

  const openEditTracking = (position: TrackedPosition) => {
    setEditingTrackingId(position.id);
    setEditBuyAmount(String(position.buy_amount));
    setEditBuyDate(position.buy_date);
    setEditOrderTime(position.order_time_bucket);
    setEditConfirmedNavDate(position.confirmed_nav_date ?? position.entry_price_date ?? "");
    setEditConfirmedNav(
      position.confirmed_nav !== null
        ? String(position.confirmed_nav)
        : position.entry_price !== null
          ? String(position.entry_price)
          : ""
    );
    setEditConfirmedShares(position.confirmed_shares !== null ? String(position.confirmed_shares) : "");
    setEditNote(position.note ?? "");
  };

  const buildEditTrackingPayload = () => {
    const payload: TrackedPositionPatchPayload = {};
    const buyAmount = positiveNumberOrUndefined(editBuyAmount, "买入金额");
    const confirmedNav = positiveNumberOrUndefined(editConfirmedNav, "实际成交价/确认净值");
    const confirmedShares = positiveNumberOrUndefined(editConfirmedShares, "实际成交份额/确认份额");
    if (buyAmount !== undefined) {
      payload.buy_amount = buyAmount;
    }
    if (editBuyDate) {
      payload.buy_date = editBuyDate;
    }
    payload.order_time_bucket = editOrderTime;
    if (editConfirmedNavDate) {
      payload.confirmed_nav_date = editConfirmedNavDate;
    }
    if (confirmedNav !== undefined) {
      payload.confirmed_nav = confirmedNav;
    }
    if (confirmedShares !== undefined) {
      payload.confirmed_shares = confirmedShares;
    }
    if (editNote.trim()) {
      payload.note = editNote.trim();
    }
    return payload;
  };

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

  const updateTracking = useMutation({
    mutationFn: async () => {
      if (editingTrackingId === null) {
        throw new Error("请先选择要编辑的追踪记录");
      }
      return (
        await api.patch<TrackedPosition>(`/api/tracked-positions/${editingTrackingId}`, buildEditTrackingPayload())
      ).data;
    },
    onSuccess: async () => {
      resetEditTracking();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["tracked-positions"] }),
        queryClient.invalidateQueries({ queryKey: ["tracked-position"] }),
        queryClient.invalidateQueries({ queryKey: ["tracked-position-audit"] }),
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

  const statusData = status.data;
  const currentLatestDate =
    statusData?.latest_data_date ??
    (etfLiveData?.signal_as_of_date ?? shortAssetData?.as_of_date) ??
    null;
  const etfLiveStatus = effectiveEtfLiveStatus;
  const currentDataIssueCount =
    assetType === "etf"
      ? (statusData?.etf_data_stale_count ?? 0) + (statusData?.etf_failed_count ?? 0)
      : statusData?.data_issue_count ?? 0;
  const selectedKey = selected ? `${selected.asset_type}-${selected.code}` : null;
  const selectedListItem = selected
    ? visibleAssets.find((item) => getItemAssetType(item) === selected.asset_type && toEtfItemCode(item) === selected.code)
    : undefined;
  const selectedPreviewAsset = selectedListItem ? previewAssetFromRankedItem(selectedListItem, etfLiveData?.market_status) : null;
  const selectedDetailDataKey = selectedDetail.data
    ? `${selectedDetail.data.asset.asset_type}-${selectedDetail.data.asset.code}`
    : null;
  const selectedDetailForCurrent = selectedKey && selectedDetailDataKey === selectedKey ? selectedDetail.data : undefined;
  const selectedAsset = selectedDetailForCurrent?.asset ?? selectedPreviewAsset;
  const selectedAssetKey = selectedAsset ? `${selectedAsset.asset_type}-${selectedAsset.code}` : null;
  const isSelectedDetailPending = Boolean(selectedKey && selectedAsset && !selectedDetailForCurrent && selectedDetail.isFetching);
  const advisorReport = selectedDetailForCurrent?.asset.advisor_report ?? null;
  const detailPoints = chartPoints(selectedDetailForCurrent);
  const windowPoints = returnWindowChart(selectedAsset ?? undefined);
  const totalAssetCount = assets.data?.total ?? 0;
  useEffect(() => {
    const lastValidOffset = Math.max(0, Math.floor(Math.max(0, totalAssetCount - 1) / ASSET_PAGE_SIZE) * ASSET_PAGE_SIZE);
    if (assetOffset > lastValidOffset) {
      setAssetOffset(lastValidOffset);
    }
  }, [assetOffset, totalAssetCount]);
  const latestIntradayQuoteTime = useMemo(() => {
    const quoteTimes = visibleAssets
      .map((item) => (isLiveRankingItem(item) ? item.quote?.quote_time : null))
      .filter((value): value is string => Boolean(value));
    return quoteTimes.sort().at(-1) ?? null;
  }, [visibleAssets]);
  const goToPreviousAssetPage = () => setAssetOffset((value) => Math.max(0, value - ASSET_PAGE_SIZE));
  const goToNextAssetPage = () => setAssetOffset((value) => value + ASSET_PAGE_SIZE);
  const observableCount = visibleAssets.filter((item) => itemConclusion(item) === "短线观察").length;
  const highRiskCount = visibleAssets.filter((item) => itemConclusion(item) === "高位观察").length;
  const dataIssues =
    statusData?.data_health
      .filter((item) => item.asset_type === assetType && (item.status !== "success" || item.is_stale))
      .slice(0, 6) ?? [];
  const selectedTracked = activeTracked.filter(
    (item) => selectedAsset && item.asset_type === selectedAsset.asset_type && item.asset_code === selectedAsset.code
  );
  const primaryTracked = selectedTracked[0] ?? null;
  const selectedLiveItem = selectedAsset
    ? visibleAssets.find((item) => isLiveRankingItem(item) && item.etf_code === selectedAsset.code)
    : null;
  const selectedLiveFallbackItem =
    selectedAsset?.asset_type === "etf"
      ? selectedEtfLiveData.data?.items.find((item) => item.etf_code === selectedAsset.code) ?? null
      : null;
  const selectedLiveRankingItem =
    selectedLiveItem && isLiveRankingItem(selectedLiveItem) ? selectedLiveItem : selectedLiveFallbackItem;
  const selectedLiveQuote = selectedLiveRankingItem?.quote ?? null;
  const selectedMarketStatus = etfLiveData?.market_status ?? selectedEtfLiveData.data?.market_status ?? effectiveEtfLiveStatus?.market_status;
  const selectedIntradayChange = intradayChangeDisplay(selectedLiveQuote, selectedMarketStatus);
  const selectedEntryTiming =
    assetType === "etf" && selectedLiveRankingItem
      ? itemEntryTimingDisplay(selectedLiveRankingItem, selectedMarketStatus)
      : {
          title: "今日买点",
          label: selectedAsset?.entry_timing_label ?? "暂无",
          reason: selectedAsset?.entry_timing_reason ?? "暂无买点原因",
        };
  const selectedDailyEntryTiming =
    assetType === "etf" && selectedLiveRankingItem
      ? {
          title: "日线买点参考",
          label: selectedLiveRankingItem.daily_entry_timing_label,
          reason: dailyReferenceReason(
            selectedLiveRankingItem.daily_entry_timing_reason,
            etfLiveData?.signal_as_of_date ?? selectedEtfLiveData.data?.signal_as_of_date,
          ),
          parts: dailyReferenceParts(
            selectedLiveRankingItem.daily_entry_timing_reason,
            etfLiveData?.signal_as_of_date ?? selectedEtfLiveData.data?.signal_as_of_date,
          ),
        }
      : null;
  const selectedAssetMetrics = selectedAsset?.metrics ?? {};
  const validationGroups = labelValidationGroups(statusData);
  const scoreBucketGroups = scoreBucketValidationGroups(statusData);
  const scoreBucketCumulativeGroups = scoreBucketGroups.filter((item) => item.group_type === "cumulative");
  const scoreBucketMarginalGroups = scoreBucketGroups.filter((item) => item.group_type === "marginal");
  const scoreBucketBaselineGroup = scoreBucketGroups.find((item) => item.group_type === "baseline");
  const scoreBucketPrimaryGroup = scoreBucketGroups.find(
    (item) => item.label === "Top 10" && item.entry_timing_label === "cumulative"
  );
  const scoreBucketValidation = statusData?.score_bucket_validation ?? {};
  const scoreBucketExcludedSources = Array.isArray(scoreBucketValidation.excluded_source_snapshots)
    ? scoreBucketValidation.excluded_source_snapshots.filter(
        (item): item is { source_signal_run_id?: number; source_date?: string; reason?: string } =>
          typeof item === "object" && item !== null
      )
    : [];
  const scoreBucketSourceRuns =
    typeof scoreBucketValidation.source_signal_run_count === "number" ? scoreBucketValidation.source_signal_run_count : 0;
  const scoreBucketScoredItems =
    typeof scoreBucketValidation.scored_item_count === "number" ? scoreBucketValidation.scored_item_count : 0;
  const scoreBucketExcludedItems =
    typeof scoreBucketValidation.excluded_unavailable_score_count === "number"
      ? scoreBucketValidation.excluded_unavailable_score_count
      : 0;
  const selectedValidationEntryLabel = selectedDailyEntryTiming?.label ?? selectedEntryTiming.label;
  const selectedValidationGroup = selectedAsset
    ? validationGroups.find(
        (item) => item.label === selectedAsset.conclusion && item.entry_timing_label === selectedValidationEntryLabel
      )
    : undefined;
  const selectedHistoricalEvidence = evidenceTrack(selectedAsset, "historical_replay");
  const selectedForwardEvidence = evidenceTrack(selectedAsset, "forward_live");
  const selectedHistoricalSummary = validationTrackSummary(selectedHistoricalEvidence, true);
  const selectedForwardSummary = validationTrackSummary(selectedForwardEvidence);
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
    queryKey: ["tracked-position", user?.id ?? "anonymous", primaryTracked?.id],
    enabled: Boolean(user) && primaryTracked !== null,
    queryFn: async ({ signal }) =>
      (await api.get<TrackedPositionDetail>(`/api/tracked-positions/${primaryTracked?.id}`, { signal })).data
  });
  const trackedAudit = useQuery({
    queryKey: ["tracked-position-audit", user?.id ?? "anonymous", primaryTracked?.id],
    enabled: Boolean(user) && primaryTracked !== null,
    queryFn: async ({ signal }) =>
      (await api.get<TrackedPositionAlertAuditList>(`/api/tracked-positions/${primaryTracked?.id}/audit?limit=20`, { signal })).data
  });
  const auditItems = trackedAudit.data?.items ?? [];
  const trackingPoints = trackingChartPoints(trackedDetail.data);
  const trackingEntry = trackingPoints.find((point) => point.isEntry);
  const trackingHigh = trackingPoints.find((point) => point.isHigh);
  const trackingCurrent = trackingPoints.find((point) => point.isCurrent);

  const refreshTrackedLifecycle = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["tracked-positions"] }),
      queryClient.invalidateQueries({ queryKey: ["tracked-position"] }),
      queryClient.invalidateQueries({ queryKey: ["tracked-position-audit"] })
    ]);
  };

  const emptyActionExecutionDraft = (): ActionExecutionDraft => ({
    executedAt: "",
    quantity: "",
    price: "",
    priceSource: "owner_reported",
    fees: "0",
    resultingShares: "",
    closeFact: false
  });

  const actionExecutionDraft = (actionId: number) =>
    actionExecutionDrafts[actionId] ?? emptyActionExecutionDraft();

  const updateActionExecutionDraft = (
    actionId: number,
    patch: Partial<ActionExecutionDraft>
  ) => {
    setActionExecutionDrafts((current) => ({
      ...current,
      [actionId]: {
        ...(current[actionId] ?? emptyActionExecutionDraft()),
        ...patch
      }
    }));
  };

  const executionFactsFromDraft = (
    actionId: number
  ): TrackedPositionActionExecutionInput => {
    const draft = actionExecutionDraft(actionId);
    const executedAt = new Date(draft.executedAt);
    const quantity = Number(draft.quantity);
    const price = Number(draft.price);
    const fees = Number(draft.fees);
    const resultingShares = Number(draft.resultingShares);
    if (!draft.executedAt || Number.isNaN(executedAt.getTime())) {
      throw new Error("请填写实际执行时间");
    }
    if (!Number.isFinite(quantity) || quantity <= 0) {
      throw new Error("实际卖出份额必须大于 0");
    }
    if (!Number.isFinite(price) || price <= 0) {
      throw new Error("实际成交价必须大于 0");
    }
    if (!Number.isFinite(fees) || fees < 0) {
      throw new Error("费用不能为负数");
    }
    if (!Number.isFinite(resultingShares) || resultingShares < 0) {
      throw new Error("执行后剩余份额不能为负数");
    }
    return {
      executed_at: executedAt.toISOString(),
      quantity,
      price,
      price_source: draft.priceSource,
      fees,
      resulting_shares: resultingShares,
      close_fact: draft.closeFact
    };
  };

  const actionTransition = useMutation({
    mutationFn: async (request: ActionTransitionMutationRequest) => {
      const requestedPayload: TrackedPositionActionTransitionRequest = {
        transition: request.transition,
        expected_position_state_version: request.expectedPositionStateVersion,
        ...(request.execution ? { execution: request.execution } : {})
      };
      const requestIdentity = JSON.stringify([
        request.positionId,
        request.actionId,
        request.transition,
        request.execution ?? null
      ]);
      const payload =
        actionRequestPayloads.current.get(requestIdentity) ?? requestedPayload;
      actionRequestPayloads.current.set(requestIdentity, payload);
      const idempotencyKey = stableActionRequestKey(
        actionRequestKeys.current,
        requestIdentity
      );
      try {
        const result = (
          await api.post<TrackedPositionActionTransitionResponse>(
            `/api/tracked-positions/${request.positionId}/actions/${request.actionId}/transitions`,
            payload,
            { headers: { "Idempotency-Key": idempotencyKey } }
          )
        ).data;
        return { result, requestIdentity };
      } catch (error) {
        const response = isAxiosError(error) ? error.response : undefined;
        if (response?.status === 409) {
          actionRequestKeys.current.delete(requestIdentity);
          actionRequestPayloads.current.delete(requestIdentity);
          setActionNotice({
            actionId: request.actionId,
            text: "持仓状态已变化，已刷新；请核对最新目标后重新提交。"
          });
          await refreshTrackedLifecycle();
        } else if (response) {
          actionRequestKeys.current.delete(requestIdentity);
          actionRequestPayloads.current.delete(requestIdentity);
        } else {
          setActionNotice({
            actionId: request.actionId,
            text: "请求结果未知。请勿修改执行事实，直接重试会复用同一请求。"
          });
        }
        throw error;
      }
    },
    onSuccess: async ({ result, requestIdentity }) => {
      actionRequestKeys.current.delete(requestIdentity);
      actionRequestPayloads.current.delete(requestIdentity);
      setActionNotice({
        actionId: result.action_id,
        text: `操作已记录：${actionStatusLabel(result.action_status)}`
      });
      setActionExecutionDrafts((current) => {
        const next = { ...current };
        delete next[result.action_id];
        return next;
      });
      await refreshTrackedLifecycle();
    }
  });

  const mobileTabs: Array<{ id: MobileTab; label: string }> = [
    { id: "ranking", label: "榜单" },
    { id: "detail", label: "详情" },
    { id: "tracking", label: "追踪" },
    { id: "explanation", label: "说明" }
  ];
  const detailNavItems: Array<{ label: string; section: "summary" | "charts" | "advisor" | "explanation" | "tracking" }> = [
    { label: "摘要", section: "summary" },
    { label: "图表", section: "charts" },
    { label: "AI", section: "advisor" },
    { label: "说明", section: "explanation" },
    { label: "追踪", section: "tracking" }
  ];

  const scrollToDetailSection = (section: "summary" | "charts" | "advisor" | "explanation" | "tracking") => {
    const target = {
      summary: summarySectionRef,
      charts: chartsSectionRef,
      advisor: advisorSectionRef,
      explanation: explanationSectionRef,
      tracking: trackingSectionRef
    }[section].current;
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  useEffect(() => {
    if (requestedSection !== "tracking") {
      return;
    }
    setMobileTab("tracking");
    const handle = window.setTimeout(() => {
      const target = holdingsSectionRef.current ?? trackingSectionRef.current;
      if (target) {
        target.scrollIntoView({ behavior: "smooth", block: "start" });
      } else {
        window.scrollTo({ top: 0, behavior: "smooth" });
      }
    }, 80);
    return () => window.clearTimeout(handle);
  }, [requestedSection]);
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
          hardStopDistance: percentValueOrWaiting(primaryTracked.dynamic_thresholds.distance_to_hard_stop_pct),
          profitDistance: percentValueOrWaiting(primaryTracked.dynamic_thresholds.distance_to_profit_start_pct),
          givebackDistance: percentValueOrWaiting(primaryTracked.dynamic_thresholds.distance_to_trailing_giveback_pct),
          trendWeakening
        }
      : null;
    const selectedLivePrice = selectedLiveQuote?.latest_price;
    const quoteStatus = !selectedLiveQuote
      ? etfLiveData?.market_status === "lunch_break"
        ? "午休，等待下午开盘"
        : "等待盘中行情"
      : etfLiveData?.market_status !== "open"
      ? etfLiveData?.market_status === "lunch_break"
        ? "午休，使用上午最近行情"
        : "休市，使用最近公开行情"
      : selectedLiveQuote.is_stale
      ? "行情滞后，仅网页参考"
      : selectedLiveQuote.decision_eligible
      ? "新鲜盘中行情"
      : "仅网页参考";
    const intradayChangeValue = selectedLiveQuote?.change_percent;
    const hasLargeIntradayDrop = assetType === "etf" && typeof intradayChangeValue === "number" && intradayChangeValue <= -3;
    const headlinePrice =
      assetType === "etf"
        ? selectedLivePrice === null || selectedLivePrice === undefined
          ? "暂无"
          : selectedLivePrice.toFixed(4)
        : selectedAsset.latest_value === null
        ? "暂无"
        : selectedAsset.latest_value.toFixed(4);
    const themeProfile = (selectedAsset.theme_profile ?? selectedAssetMetrics.theme_profile ?? {}) as Record<string, unknown>;
    const themeText = (value: unknown, fallback = "暂无") =>
      typeof value === "string" && value.trim().length > 0 ? value : fallback;
    const secondaryThemes = Array.isArray(themeProfile.secondary_themes)
      ? themeProfile.secondary_themes.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
      : [];
    const dynamicContext = (selectedAssetMetrics.dynamic_threshold_context ?? selectedAsset.rationale.dynamic_threshold_context ?? {}) as Record<string, unknown>;
    const dynamicThresholdMap = (dynamicContext.thresholds ?? {}) as Record<string, unknown>;
    const dynamicNumber = (value: unknown) => (typeof value === "number" && Number.isFinite(value) ? value : null);
    const dynamicPercent = (key: string) => {
      const value = dynamicNumber(dynamicThresholdMap[key]);
      return value === null ? "暂无" : formatPercent(value);
    };
    const volatilityUnit = dynamicNumber(dynamicContext.volatility_unit_pct);

    return (
      <div className="mt-5 rounded-[10px] border border-ink/10 bg-white p-4">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">观察与持仓摘要</p>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded-[8px] border border-ink/10 bg-paper px-3 py-2">
            <p className="text-xs text-ink/45">{assetType === "etf" ? "盘中/最近价" : "最新净值"}</p>
            <p className="mt-1 text-base font-semibold text-ink">{headlinePrice}</p>
          </div>
          <div className="rounded-[8px] border border-ink/10 bg-paper px-3 py-2">
            <p className="text-xs text-ink/45">{assetType === "etf" ? "盘中涨跌" : "最新日涨跌"}</p>
            <p className="mt-1 text-base font-semibold text-ink">
              {assetType === "etf" ? selectedIntradayChange.value : percentMetric(selectedAssetMetrics, "today_return_pct")}
            </p>
          </div>
          <div className="rounded-[8px] border border-ink/10 bg-paper px-3 py-2">
            <p className="text-xs text-ink/45">行情时间</p>
            <p className="mt-1 text-sm font-semibold text-ink">{assetType === "etf" ? selectedIntradayChange.time : formatDate(selectedAsset.latest_date)}</p>
          </div>
          <div className="rounded-[8px] border border-ink/10 bg-paper px-3 py-2">
            <p className="text-xs text-ink/45">数据状态</p>
            <p className="mt-1 text-sm font-semibold text-ink">{assetType === "etf" ? quoteStatus : "公开净值"}</p>
          </div>
        </div>

        {hasLargeIntradayDrop ? (
          <div className="mt-3 rounded-[8px] border border-rose-200 bg-rose-50 px-3 py-2 text-sm font-semibold text-rose-800">
            盘中回撤较大：当前盘中涨跌 {formatPercent(intradayChangeValue)}，请优先看盘中风险，不要只看昨日日线趋势。
          </div>
        ) : null}

        <div className="mt-3 grid gap-3 md:grid-cols-3">
          <div className="rounded-[8px] border border-ink/10 p-3">
            <p className="text-xs font-semibold text-accent">买入观察</p>
            <p className="mt-2 text-base font-semibold text-ink">{selectedAsset.conclusion}</p>
          </div>
          <div className="rounded-[8px] border border-ink/10 p-3">
            <p className="text-xs font-semibold text-accent">{selectedEntryTiming.title}</p>
            <p className="mt-2 text-base font-semibold text-ink">{selectedEntryTiming.label}</p>
            <p className="mt-1 text-sm leading-6 text-ink/60">{selectedEntryTiming.reason}</p>
          </div>
          <div className="rounded-[8px] border border-ink/10 p-3">
            <p className="text-xs font-semibold text-accent">持仓处理</p>
            <p className="mt-2 text-base font-semibold text-ink">{selectedHoldingDecision}</p>
            <p className="mt-1 text-sm leading-6 text-ink/60">{selectedLatestReason}</p>
          </div>
        </div>

        {selectedDailyEntryTiming ? (
          <div className="mt-3 rounded-[8px] border border-ink/10 bg-paper p-3">
            <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-xs font-semibold text-accent">日线参考</p>
              <span className="w-fit rounded-full bg-white px-2.5 py-1 text-xs font-semibold text-ink/60">{selectedDailyEntryTiming.label}</span>
            </div>
            <div className="mt-2 grid gap-2 text-sm text-ink/70 md:grid-cols-2">
              {selectedDailyEntryTiming.parts.map((part) => (
                <div key={`${part.label}-${part.value}`} className="rounded-[8px] bg-white px-3 py-2">
                  <span className="text-ink/45">{part.label}：</span>
                  <span className="font-medium text-ink/75">{part.value}</span>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <div className="mt-3 grid gap-2 text-sm text-ink/70 md:grid-cols-2">
          <span>近5日涨跌：{percentMetric(selectedAssetMetrics, "return_5d")}</span>
          <span>近20日涨跌：{percentMetric(selectedAssetMetrics, "return_20d")}</span>
          <span>近60日涨跌：{percentMetric(selectedAssetMetrics, "return_60d")}</span>
          <span>60日回撤：{percentMetric(selectedAssetMetrics, "max_drawdown_60d")}</span>
          <span>波动（20日）：{percentMetric(selectedAssetMetrics, "volatility_20d")}</span>
          <span>趋势强度：{scoreOrWaiting(selectedAssetTrendScore)}</span>
          <span>数据来源：{selectedAssetSource}</span>
          <span>持仓状态：{selectedHoldingStatus}</span>
        </div>
        {assetType === "etf" ? (
          <div className="mt-3 grid gap-3 md:grid-cols-2">
            <div className="rounded-[8px] border border-ink/10 bg-paper p-3">
              <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs font-semibold text-accent">主题归类</p>
                <span className="w-fit rounded-full bg-white px-2.5 py-1 text-xs font-semibold text-ink/60">
                  {themeText(themeProfile.classification_confidence, "未知置信度")}
                </span>
              </div>
              <div className="mt-2 grid gap-2 text-sm text-ink/70 sm:grid-cols-2">
                <span>主主题：{themeText(themeProfile.primary_theme ?? selectedAsset.primary_theme, "未分类")}</span>
                <span>主题组：{themeText(themeProfile.theme_group ?? selectedAsset.theme_group, "unknown")}</span>
                <span>来源：{themeText(themeProfile.classification_source ?? selectedAsset.classification_source, "规则未命中")}</span>
                <span>副主题：{secondaryThemes.length ? secondaryThemes.join(" / ") : "暂无"}</span>
              </div>
              <p className="mt-2 text-sm leading-6 text-ink/55">
                {themeText(themeProfile.classification_reason ?? selectedAsset.classification_reason, "未找到足够明确的行业/主题证据，组合层会保守处理。")}
              </p>
            </div>
            <div className="rounded-[8px] border border-ink/10 bg-paper p-3">
              <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                <p className="text-xs font-semibold text-accent">动态阈值依据</p>
                <span className="w-fit rounded-full bg-white px-2.5 py-1 text-xs font-semibold text-ink/60">
                  {themeText(dynamicContext.rule_version, "规则版本未知")}
                </span>
              </div>
              <div className="mt-2 grid gap-2 text-sm text-ink/70 sm:grid-cols-2">
                <span>阈值模式：{themeText(dynamicContext.threshold_mode, "暂无")}</span>
                <span>波动单位：{volatilityUnit === null ? "暂无" : formatPercent(volatilityUnit)}</span>
                <span>追高线：{dynamicPercent("chase_daily")}</span>
                <span>跌破等待线：{dynamicPercent("drop_wait")}</span>
                <span>移动止盈启动：{dynamicPercent("profit_start_pct")}</span>
                <span>高点回吐线：{dynamicPercent("trailing_giveback_pct")}</span>
              </div>
            </div>
          </div>
        ) : null}
        {assetType === "etf" ? (
          <div className="mt-3 rounded-[8px] bg-paper px-3 py-3 text-xs leading-5 text-ink/60">
            <div className="mb-3 rounded-[8px] border border-ink/10 bg-white p-3">
              <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                <p className="font-semibold text-ink">证据契约</p>
                <span className="w-fit rounded-full bg-paper px-2 py-0.5 text-[11px] font-semibold text-ink/65">
                  {selectedAsset.evidence_status ?? "等待验证"}
                </span>
              </div>
              <p className="mt-2 text-ink/55">
                {evidenceContractText(selectedAsset.evidence_status, selectedAsset.evidence_summary)}
              </p>
            </div>
            <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
              <p className="font-semibold text-ink">标签验证</p>
            </div>
            <div className="mt-2 grid gap-2 md:grid-cols-2">
              {[
                ["历史回放", selectedHistoricalSummary],
                ["真实前瞻", selectedForwardSummary]
              ].map(([title, summary]) => {
                const track = summary as ReturnType<typeof validationTrackSummary>;
                return (
                  <div key={title as string} className="rounded-[8px] border border-ink/10 bg-white p-3">
                    <div className="flex items-center justify-between gap-2">
                      <p className="font-semibold text-ink">{title as string}</p>
                      <span className="rounded-full bg-paper px-2 py-0.5 text-[11px] font-semibold text-ink/65">{track.status}</span>
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-1 text-ink/65">
                      <span>5日样本：{track.sampleCount}</span>
                      <span>胜率：{track.winRate}</span>
                      <span>中位收益：{track.medianReturn}</span>
                      <span>最差回撤：{track.drawdown}</span>
                      <span>覆盖率：{track.coverage}</span>
                      <span>截至：{track.asOfDate}</span>
                    </div>
                  </div>
                );
              })}
            </div>
            <p className="mt-2">{selectedValidationGroup ? labelValidationLine(selectedValidationGroup, "10") : "样本不足时只能继续观察，不能把标签当成买入结论。"}</p>
            <p>{observationPortfolioText(selectedAsset)}</p>
          </div>
        ) : null}
        <details className="mt-3 rounded-[8px] bg-paper px-3 py-2">
          <summary className="cursor-pointer text-sm font-semibold">动态阈值</summary>
          <p className="mt-2 text-xs text-ink/65">
            {thresholds
              ? `硬止损${thresholds.hardStop}（距离${thresholds.hardStopDistance}）；止盈起点${thresholds.profitStart}（距离${thresholds.profitDistance}）；回撤减仓${thresholds.giveback}（距离${thresholds.givebackDistance}）；趋势减弱预警${thresholds.trendWeakening}`
              : "暂无追踪动态阈值"}
          </p>
        </details>
      </div>
    );
  };

  const isMobileLayout = () => typeof window !== "undefined" && window.matchMedia("(max-width: 1023px)").matches;

  const selectAsset = (item: RankedAssetItem) => {
    const nextSelected = { asset_type: getItemAssetType(item), code: toEtfItemCode(item) };
    setSelected(nextSelected);
    if (isMobileLayout()) {
      setMobileTab("detail");
    } else {
      setPendingDesktopScrollKey(`${nextSelected.asset_type}-${nextSelected.code}`);
    }
  };

  useEffect(() => {
    if (!pendingDesktopScrollKey || !selectedAssetKey) {
      return;
    }
    if (selectedAssetKey !== pendingDesktopScrollKey) {
      return;
    }
    const frame = window.requestAnimationFrame(() => {
      summarySectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      setPendingDesktopScrollKey(null);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [pendingDesktopScrollKey, selectedAssetKey]);

  useEffect(() => {
    if (!visibleAssets.length) {
      setSelected(null);
      setMobileTab("ranking");
    }
  }, [visibleAssets.length]);

  const setQuickLabelFilter = (preset: "safe" | "high" | "tracked" | "clear") => {
    if (preset === "clear") {
      setLabelFilters(emptyLabelFilters);
      return;
    }
    if (preset === "tracked") {
      setLabelFilters({ observation: [], entry: [], tracking: ["我已持仓"] });
      return;
    }
    if (preset === "high") {
      setLabelFilters({ observation: ["高位观察"], entry: ["健康回踩", "趋势延续"], tracking: [] });
      return;
    }
    setLabelFilters({ observation: ["短线观察"], entry: ["健康回踩", "趋势延续"], tracking: [] });
  };

  const toggleLabelFilter = (groupKey: LabelFilterKey, value: string) => {
    setLabelFilters((current) => {
      const currentValues = current[groupKey];
      const nextValues = currentValues.includes(value)
        ? currentValues.filter((item) => item !== value)
        : [...currentValues, value];
      return { ...current, [groupKey]: nextValues };
    });
  };

  const removeLabelFilter = (groupKey: LabelFilterKey, value: string) => {
    setLabelFilters((current) => ({
      ...current,
      [groupKey]: current[groupKey].filter((item) => item !== value),
    }));
  };

  const mobileTrackingButtonLabel = trackingOpen ? "收起" : "我已买入，开始追踪";
  const labelFilterPanel = (
    <div className="mt-4 rounded-[8px] border border-border bg-white p-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">标签筛选</p>
        </div>
        <span className="w-fit rounded-full bg-paper px-3 py-1 text-xs font-semibold text-ink/60">
          已选 {activeLabelFilterCount} 项
        </span>
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        <button
          type="button"
          className="rounded-full border border-border bg-paper px-3 py-1.5 text-xs font-semibold text-ink transition hover:border-ink focus:outline-none focus:ring-2 focus:ring-accent/20"
          onClick={() => setQuickLabelFilter("safe")}
        >
          稳妥观察
        </button>
        <button
          type="button"
          className="rounded-full border border-border bg-paper px-3 py-1.5 text-xs font-semibold text-ink transition hover:border-ink focus:outline-none focus:ring-2 focus:ring-accent/20"
          onClick={() => setQuickLabelFilter("high")}
        >
          高位谨慎
        </button>
        <button
          type="button"
          className="rounded-full border border-border bg-paper px-3 py-1.5 text-xs font-semibold text-ink transition hover:border-ink focus:outline-none focus:ring-2 focus:ring-accent/20"
          onClick={() => setQuickLabelFilter("tracked")}
        >
          只看持仓
        </button>
        <button
          type="button"
          className="rounded-full border border-border bg-white px-3 py-1.5 text-xs font-semibold text-ink/60 transition hover:border-ink focus:outline-none focus:ring-2 focus:ring-accent/20"
          onClick={() => setQuickLabelFilter("clear")}
        >
          清空
        </button>
        <button
          type="button"
          className="rounded-full border border-border bg-white px-3 py-1.5 text-xs font-semibold text-ink/60 transition hover:border-ink focus:outline-none focus:ring-2 focus:ring-accent/20"
          onClick={() => setLabelFilterExpanded((value) => !value)}
        >
          {showLabelFilterDetails ? "收起标签" : "展开标签"}
        </button>
      </div>
      {showLabelFilterDetails ? (
        <div className="mt-4 grid gap-3 md:grid-cols-3">
          {labelFilterGroups.map((group) => (
            <fieldset key={group.key} className="rounded-[8px] border border-border bg-paper/50 p-3">
              <legend className="px-1 text-xs font-semibold text-ink">{group.title}</legend>
              <div className="mt-2 grid gap-2">
                {group.options.map((option) => {
                  const checked = labelFilters[group.key].includes(option);
                  return (
                    <label key={option} className="flex cursor-pointer items-center gap-2 text-xs text-ink/70">
                      <input
                        type="checkbox"
                        className="h-3.5 w-3.5 rounded border-border text-ink focus:ring-2 focus:ring-accent/20"
                        checked={checked}
                        onChange={() => toggleLabelFilter(group.key, option)}
                      />
                      <span className={checked ? "font-semibold text-ink" : ""}>{option}</span>
                    </label>
                  );
                })}
              </div>
            </fieldset>
          ))}
        </div>
      ) : null}
      {selectedLabelChips.length ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {selectedLabelChips.map((chip) => (
            <button
              key={`${chip.groupKey}-${chip.value}`}
              type="button"
              className="rounded-full border border-ink/10 bg-white px-3 py-1.5 text-xs font-semibold text-ink transition hover:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20"
              onClick={() => removeLabelFilter(chip.groupKey, chip.value)}
            >
              {chip.groupTitle}：{chip.value} ×
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
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
          <span className="w-fit rounded-full bg-blush px-2.5 py-1 text-xs font-semibold text-ink/60">
            每页 12 条
          </span>
        </div>
        {assetType === "etf" ? (
          <div className="mb-4 rounded-[8px] border border-border bg-paper p-3">
            <div className="flex flex-wrap gap-2">
              {(
                [
                  ["research", "研究榜（默认）"],
                  ["actionable", "可行动榜"]
                ] as const
              ).map(([surface, label]) => (
                <button
                  key={surface}
                  type="button"
                  className={`rounded-full px-3 py-1.5 text-xs font-semibold transition ${
                    rankingSurface === surface
                      ? "bg-ink text-white"
                      : "border border-ink/10 bg-white text-ink/65 hover:border-accent"
                  }`}
                  onClick={() => {
                    setRankingSurface(surface);
                    setAssetOffset(0);
                    setSelected(null);
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="mt-2 text-xs leading-5 text-ink/55">
              {rankingSurface === "research"
                ? "研究榜只使用可追溯复权日线，覆盖更广；行动资格与行动名次单独展示。"
                : "可行动榜仅保留具备同日买卖价、IOPV、溢折价、健康 provider 与一致性证据的 ETF；缺失时不使用回退候选。"}
            </p>
            <p className="mt-1 text-xs text-ink/45">
              覆盖率：
              {shortAssetData?.snapshot?.coverage_ratio === null ||
              shortAssetData?.snapshot?.coverage_ratio === undefined
                ? "暂无"
                : `${(shortAssetData.snapshot.coverage_ratio * 100).toFixed(1)}%`}
              {" · "}截至 {formatDate(shortAssetData?.snapshot?.as_of_trade_date)}
              {" · "}生成于 {formatUtcDateTime(shortAssetData?.snapshot?.generated_at)}
            </p>
          </div>
        ) : null}
        <div className={`grid gap-3 ${compact ? "" : "md:grid-cols-2"}`}>
          <div className="rounded-[6px] border border-border bg-white px-3 py-2 text-sm font-medium text-ink">{mode.classification}</div>
          <input
            className="rounded-[6px] border border-border bg-white px-3 py-2 text-sm outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/15"
            placeholder="搜索代码/名称"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
          />
          <select
            className="rounded-[6px] border border-border bg-white px-3 py-2 text-sm outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/15"
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
            className="rounded-[6px] border border-border bg-white px-3 py-2 text-sm outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/15"
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
        {assetType === "etf" ? labelFilterPanel : null}
        {assetType === "etf" && (etfThemeSource.data?.theme_heat?.length ?? 0) > 0 ? (
          <div className="mt-4 rounded-[8px] border border-border bg-white p-3">
            <div className="flex items-center justify-between gap-3">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">主题热度</p>
              <span className="text-xs text-ink/45">按最新短线排序缓存统计</span>
            </div>
            <div className="mt-3 flex gap-2 overflow-x-auto pb-1">
              {etfThemeSource.data?.theme_heat?.slice(0, 8).map((item) => (
                <button
                  key={item.theme}
                  type="button"
                  className={`shrink-0 rounded-full border px-3 py-1.5 text-xs transition ${
                    theme === item.theme
                      ? "border-ink bg-ink text-white"
                      : "border-border bg-paper text-ink/70 hover:border-accent"
                  }`}
                  onClick={() => setTheme(item.theme)}
                >
                  {item.theme} · {item.count}只 · {item.avg_score.toFixed(1)}分
                </button>
              ))}
            </div>
          </div>
        ) : null}
        <div className="mt-5">
          <AssetPaginationBar
            total={totalAssetCount}
            offset={assetOffset}
            visibleCount={visibleAssets.length}
            isFetching={assets.isFetching}
            scopeLabel={assetCountScope}
            onPrevious={goToPreviousAssetPage}
            onNext={goToNextAssetPage}
          />
        </div>
        <div className={`mt-5 ${listGap}`}>
          {assets.isLoading ? (
            <div className="rounded-[10px] border border-dashed border-ink/20 p-6 text-sm text-ink/55">正在加载榜单...</div>
          ) : null}
          {assets.isError ? (
            <div className="rounded-[10px] border border-rose-200 bg-rose-50 p-6 text-sm leading-6 text-rose-800">
              榜单请求失败：{errorText(assets.error)}
              <button type="button" className="ml-2 font-semibold underline underline-offset-2" onClick={() => void assets.refetch()}>
                重试
              </button>
            </div>
          ) : null}
          {visibleAssets.map((item) => {
            const itemAssetType = getItemAssetType(item);
            const itemCode = toEtfItemCode(item);
            const isSelected = selected?.asset_type === itemAssetType && selected.code === itemCode;
            const trackedForItem = trackedByAsset.get(`${itemAssetType}-${itemCode}`);
            const isLiveItem = isLiveRankingItem(item);
            const quote = liveRankingQuote(item);
            const livePrice = quote?.latest_price;
            const timingDisplay = itemEntryTimingDisplay(item, etfLiveData?.market_status);
            const liveChange = intradayChangeDisplay(quote, etfLiveData?.market_status);
            const itemScoreVersion = isLiveItem
              ? item.score_version === "final_score_v3"
                ? "当前评分口径"
                : item.score_version
                ? "旧口径结果"
                : "暂无口径"
              : scoreVersionText(item as ShortResearchAsset);
            return (
              <button
                key={`${itemAssetType}-${itemCode}`}
                className={`w-full ${compact ? "rounded-[8px]" : "rounded-[10px]"} ${cardPadding} text-left transition ${
                  isSelected ? "border-ink bg-ink text-white" : "border-ink/10 bg-white text-ink hover:border-accent"
                }`}
                onClick={() => selectAsset(item)}
              >
                <div className={`flex flex-col ${cardGap} md:flex-row md:items-start md:justify-between`}>
                  <div className="min-w-0">
                    <p className={`text-xs font-semibold ${isSelected ? "text-white/60" : "text-accent"}`}>
                      {isLiveItem ? "实时排名" : `#${item.rank ?? "-"}`}
                      {isLiveItem ? "" : ` · ${assetTypeLabel(item.asset_type)}`}
                      {!isLiveItem ? ` · ${shortResearchThemeTags(item).slice(0, 3).join(" / ")}` : ""}
                    </p>
                    <h3 className="mt-2 text-xl font-semibold leading-tight">
                      {isLiveItem ? toEtfItemName(item) : item.name}
                      <span className={`ml-2 text-sm font-normal ${isSelected ? "text-white/45" : "text-ink/45"}`}>
                        {isLiveItem ? itemCode : item.code}
                      </span>
                    </h3>
                    <p className={`mt-2 line-clamp-2 text-sm leading-6 ${isSelected ? "text-white/65" : "text-ink/60"}`}>
                      {timingDisplay.reason}
                    </p>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white text-ink" : "bg-ink text-white"}`}>
                      {isLiveItem
                        ? liveScoreText(item)
                        : assetScoreText(item)}
                    </span>
                    {!isLiveItem && item.asset_type === "etf" && item.opportunity_score !== null && item.opportunity_score !== undefined ? (
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white/15 text-white" : "bg-accent text-white"}`}>
                        {opportunityScoreText(item)}
                      </span>
                    ) : null}
                    <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white/15 text-white" : "bg-paper text-ink/60"}`}>
                      {itemScoreVersion}
                    </span>
                    <span
                      className={`rounded-full px-3 py-1 text-xs font-semibold ${
                        isSelected ? "bg-white/15 text-white" : conclusionTone(itemConclusion(item))
                      }`}
                    >
                      买入观察状态：{itemConclusion(item)}
                    </span>
                    <span
                      className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white/15 text-white" : entryTimingTone(timingDisplay.label)}`}
                    >
                      {timingDisplay.title}：{timingDisplay.label}
                    </span>
                    {isLiveItem ? (
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white/15 text-white" : ""}`}>
                        实时排名变化：{etfLiveRankChangeText(item.rank_change)}
                      </span>
                    ) : item.advisor_report ? (
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white/15 text-white" : advisorTone(item.advisor_report.action_label)}`}>
                        {item.advisor_report.action_label}
                      </span>
                    ) : null}
                    {trackedForItem?.exit_signal ? (
                      <span
                        className={`rounded-full px-3 py-1 text-xs font-semibold ${
                          isSelected ? "bg-white/15 text-white" : exitSignalTone(trackedForItem.exit_signal.level)
                        }`}
                      >
                        持仓处理：{trackedForItem.exit_signal.label}
                      </span>
                    ) : null}
                  </div>
                </div>
                <div className={`mt-4 grid gap-2 text-sm sm:grid-cols-2 ${isSelected ? "text-white/75" : "text-ink/65"}`}>
                  {isLiveItem ? (
                    <>
                      <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                        全局排名 #{etfGlobalRank(item) ?? "-"}
                      </span>
                      <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                        当前范围排名 #{toEtfItemRank(item) ?? "-"} · 筛选位置 #{etfFilteredPosition(item) ?? "-"}
                      </span>
                      <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                        当前价：{quote ? (livePrice === null || livePrice === undefined ? "暂无" : livePrice.toFixed(4)) : "暂无"}
                      </span>
                      <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                        盘中涨跌：{liveChange.value}
                      </span>
                      <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                        行情：{liveChange.time}
                      </span>
                      <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                        排名变化：{etfLiveRankChangeText(item.rank_change)}
                      </span>
                      <span className={`rounded-[12px] px-3 py-2 sm:col-span-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                        分数来源：{item.score_contribution_reasons.slice(0, 2).join("；") || "等待盘中数据"}
                      </span>
                      <span className={`rounded-[12px] px-3 py-2 sm:col-span-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                        {quoteReliabilityLine(quote)}
                      </span>
                    </>
                  ) : (
                    <>
                      <span className={`rounded-[12px] px-3 py-2 sm:col-span-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                        {validationEvidenceText(item as ShortResearchAsset)}
                      </span>
                      {item.asset_type === "etf" ? (
                        <>
                          <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                            研究榜 #{item.research_rank ?? "-"} · {formatOptionalScore(item.research_score)} 分
                          </span>
                          <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                            {historyTierText(item.history_confidence_tier)}
                          </span>
                          <span className={`rounded-[12px] px-3 py-2 sm:col-span-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                            {actionableStateText(item)}
                          </span>
                          {item.sector_trend_score !== null && item.sector_trend_score !== undefined ? (
                            <span className={`rounded-[12px] px-3 py-2 sm:col-span-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                              板块趋势：{sectorTrendText(item)}
                            </span>
                          ) : null}
                          <span className={`rounded-[12px] px-3 py-2 sm:col-span-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                            主题催化：{catalystSummaryText(item)}
                          </span>
                          {item.factor_profile_version ? (
                            <span className={`rounded-[12px] px-3 py-2 sm:col-span-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                              {factorProfileText(item)}
                            </span>
                          ) : null}
                        </>
                      ) : null}
                      <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                        最新日涨跌：{percentMetric(item.metrics, "today_return_pct")}
                      </span>
                      <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                        近 20 日：{percentMetric(item.metrics, "return_20d")}
                      </span>
                      <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                        60 日回撤：{percentMetric(item.metrics, "max_drawdown_60d")}
                      </span>
                      <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                        样本：{item.usable_days} 天
                      </span>
                    </>
                  )}
                </div>
              </button>
            );
          })}
          {!assets.isLoading && rawAssets.length > 0 && visibleAssets.length === 0 ? (
            <div className="rounded-[10px] border border-dashed border-ink/20 p-6 text-sm leading-6 text-ink/55">
              当前页没有匹配这些标签的 ETF。可以清空标签筛选、换一页，或调整主题/搜索条件。
              <button
                type="button"
                className="ml-2 font-semibold text-accent underline underline-offset-2"
                onClick={() => setQuickLabelFilter("clear")}
              >
                清空筛选
              </button>
            </div>
          ) : null}
          {!assets.isLoading && !assets.isError && rawAssets.length === 0 ? (
            <div className="rounded-[10px] border border-dashed border-ink/20 p-6 text-sm leading-6 text-ink/55">
              {assetType === "etf" && rankingSurface === "actionable"
                ? "当前没有通过行动合同的 ETF。系统已按 provider 健康、同日时间戳和必填执行字段失败关闭，不会用研究榜、旧行情或估算数据补候选。"
                : mode.noResults}
            </div>
          ) : null}
          <AssetPaginationBar
            total={totalAssetCount}
            offset={assetOffset}
            visibleCount={visibleAssets.length}
            isFetching={assets.isFetching}
            scopeLabel={assetCountScope}
            onPrevious={goToPreviousAssetPage}
            onNext={goToNextAssetPage}
          />
        </div>
      </>
    );
  };

  const renderMobileDetailPanel = () => {
    if (!selectedAsset) {
      return <div className="rounded-[10px] border border-dashed border-ink/20 p-6 text-sm leading-6 text-ink/55">{mode.detailEmpty}</div>;
    }
    return (
      <Panel className="rounded-[12px]">
        <div className="flex flex-col gap-3">
          <div>
            <p className="text-sm text-ink/50">
              {assetTypeLabel(selectedAsset.asset_type)} · {selectedAsset.trading_rule_label}
            </p>
            <h2 className="mt-2 text-2xl font-semibold text-ink">
              {selectedAsset.name}
              <span className="ml-2 text-lg font-normal text-ink/45">{selectedAsset.code}</span>
            </h2>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-[8px] bg-paper px-4 py-3">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">当前结论</p>
              <p className="mt-2 text-lg font-semibold text-ink">
                {selectedAsset.conclusion} · {assetScoreSummaryText(selectedAsset)}
              </p>
              <p className="mt-2 text-sm leading-6 text-ink/65">
                {rationaleText(selectedAsset, "key_reason", "暂无")}
              </p>
            </div>
            <div className="rounded-[8px] bg-paper px-4 py-3">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">{selectedEntryTiming.title}</p>
              <p className="mt-2 text-lg font-semibold text-ink">{selectedEntryTiming.label}</p>
              <p className="mt-2 text-sm leading-6 text-ink/65">{selectedEntryTiming.reason}</p>
            </div>
            <div className="rounded-[8px] bg-ink p-4 text-white">
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-white/50">持有建议</p>
              <p className="mt-2 text-sm leading-7 text-white/75">
                {rationaleText(selectedAsset, "holding_plan", "建议结合数据与风险控制后再操作。")}
              </p>
            </div>
            {selectedAsset.asset_type === "etf" ? (
              <div className="rounded-[8px] bg-paper px-4 py-3 sm:col-span-2">
                <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">研究榜与行动资格</p>
                <p className="mt-2 text-sm leading-6 text-ink/65">
                  研究榜 #{selectedAsset.research_rank ?? "-"} · {formatOptionalScore(selectedAsset.research_score)} 分
                  {"；"}{historyTierText(selectedAsset.history_confidence_tier)}
                </p>
                <p className="mt-1 text-sm leading-6 text-ink/65">{actionableStateText(selectedAsset)}</p>
                <p className="mt-3 text-xs font-semibold uppercase tracking-[0.18em] text-accent">主题/板块辅助</p>
                <p className="mt-2 text-base font-semibold text-ink">
                  {opportunityStatusText(selectedAsset)}
                </p>
                <p className="mt-1 text-xs text-ink/50">{opportunityVersionText(selectedAsset)}</p>
                <p className="mt-2 text-sm leading-6 text-ink/65">板块趋势：{sectorTrendText(selectedAsset)}</p>
              </div>
            ) : null}
            {selectedAsset.asset_type === "etf" ? (
              <div className="sm:col-span-2">
                <CatalystShadowPanel asset={selectedAsset} />
              </div>
            ) : null}
          </div>

          {assetType === "etf" ? (
            <p className="rounded-[8px] bg-paper px-4 py-3 text-sm leading-6 text-ink/65">{quoteReliabilityLine(selectedLiveQuote)}</p>
          ) : null}
          <div className={`grid gap-2 text-sm ${assetType === "etf" ? "sm:grid-cols-4" : "sm:grid-cols-5"}`}>
            <StatPill label={mode.latestLabel} value={formatDate(selectedAsset.latest_date)} tone="bg-white text-ink" />
            <StatPill label={mode.priceLabel} value={selectedAsset.latest_value === null ? "暂无" : selectedAsset.latest_value.toFixed(4)} />
            {assetType === "etf" ? (
              <StatPill label="盘中涨跌" value={selectedIntradayChange.value} tone="bg-white text-ink" />
            ) : null}
            {assetType === "etf" ? (
              <StatPill label="行情时间" value={selectedIntradayChange.time} tone="bg-white text-ink" />
            ) : null}
            <StatPill label="样本天数" value={`${selectedAsset.usable_days} 天`} tone="bg-accentSoft text-ink" />
            <StatPill label="样本标签" value={selectedAsset.conclusion} tone="bg-white text-ink" />
            <StatPill label={assetType === "etf" ? "盘中买点" : "今日买点"} value={selectedEntryTiming.label} tone="bg-white text-ink" />
            {assetType === "etf" ? (
              <StatPill
                label="20日换手"
                value={formatTurnover(numericMetric(selectedAsset.metrics, "average_turnover_20d"))}
                tone="bg-white text-ink"
              />
            ) : null}
          </div>

          {renderAssetStatusSummary()}

          <div className="rounded-[10px] bg-ink text-white p-4">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <p className="text-sm font-semibold">追踪入口</p>
                <p className="mt-1 text-sm leading-6 text-white/75">提交买入记录并开始追踪该标的，显示持仓与预警信息。</p>
              </div>
              <button
                className="rounded-[6px] bg-accent px-4 py-2.5 text-sm font-medium text-white transition hover:bg-accent/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
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
                    className="mt-2 w-full rounded-[6px] border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
                    inputMode="decimal"
                    value={trackingAmount}
                    onChange={(event) => setTrackingAmount(event.target.value)}
                  />
                </label>
                <label className="text-sm text-ink/65">
                  买入日期
                  <input
                    className="mt-2 w-full rounded-[6px] border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
                    type="date"
                    value={trackingDate}
                    onChange={(event) => setTrackingDate(event.target.value)}
                  />
                </label>
                <label className="text-sm text-ink/65">
                  下单时段
                  <select
                    className="mt-2 w-full rounded-[6px] border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
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
                    className="mt-2 w-full rounded-[6px] border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
                    type="date"
                    value={trackingConfirmedNavDate}
                    onChange={(event) => setTrackingConfirmedNavDate(event.target.value)}
                  />
                </label>
                <label className="text-sm text-ink/65">
                  {assetType === "etf" ? "实际成交价（可选）" : "支付宝确认净值（可选）"}
                  <input
                    className="mt-2 w-full rounded-[6px] border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
                    inputMode="decimal"
                    placeholder={assetType === "etf" ? "例如：0.815" : "例如：7.3130"}
                    value={trackingConfirmedNav}
                    onChange={(event) => setTrackingConfirmedNav(event.target.value)}
                  />
                </label>
                <label className="text-sm text-ink/65">
                  {assetType === "etf" ? "实际成交份额（可选）" : "支付宝确认份额（可选）"}
                  <input
                    className="mt-2 w-full rounded-[6px] border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
                    inputMode="decimal"
                    placeholder="确认份额最准确"
                    value={trackingConfirmedShares}
                    onChange={(event) => setTrackingConfirmedShares(event.target.value)}
                  />
                </label>
                <label className="text-sm text-ink/65">
                  备注
                  <input
                    className="mt-2 w-full rounded-[6px] border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
                    placeholder="可选备注"
                    value={trackingNote}
                    onChange={(event) => setTrackingNote(event.target.value)}
                  />
                </label>
                <button
                  className="self-end rounded-[6px] bg-ink px-4 py-2.5 text-sm font-medium text-white transition hover:bg-ink/90 disabled:opacity-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                  disabled={createTracking.isPending || Number(trackingAmount) <= 0}
                  onClick={() => createTracking.mutate()}
                >
                  {createTracking.isPending ? "保存中..." : "保存追踪"}
                </button>
              </div>
            ) : null}
            {createTracking.isError ? (
              <p className="mt-3 rounded-[8px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
                保存追踪失败：{errorText(createTracking.error)}
              </p>
            ) : null}
          </div>
        </div>
      </Panel>
    );
  };

  const renderTrackingEditForm = (item: TrackedPosition) => {
    if (editingTrackingId !== item.id) {
      return null;
    }
    const priceLabel = item.asset_type === "etf" ? "实际成交价" : "确认净值";
    const sharesLabel = item.asset_type === "etf" ? "实际成交份额" : "确认份额";
    const priceDateLabel = item.asset_type === "etf" ? "买入价格日" : "确认净值日";
    return (
      <div className="mt-4 rounded-[10px] border border-ink/10 bg-paper p-4">
        <p className="text-sm font-semibold text-ink">编辑买入信息</p>
        <div className="mt-3 grid gap-3 text-sm sm:grid-cols-2">
          <label className="text-ink/65">
            买入金额
            <input
              className="mt-1 w-full rounded-[6px] border border-ink/10 bg-white px-3 py-2 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
              inputMode="decimal"
              value={editBuyAmount}
              onChange={(event) => setEditBuyAmount(event.target.value)}
            />
          </label>
          <label className="text-ink/65">
            下单日期
            <input
              type="date"
              className="mt-1 w-full rounded-[6px] border border-ink/10 bg-white px-3 py-2 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
              value={editBuyDate}
              onChange={(event) => setEditBuyDate(event.target.value)}
            />
          </label>
          <label className="text-ink/65">
            下单时间
            <select
              className="mt-1 w-full rounded-[6px] border border-ink/10 bg-white px-3 py-2 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
              value={editOrderTime}
              onChange={(event) => setEditOrderTime(event.target.value as OrderTimeBucket)}
            >
              <option value="before_15">15:00 前</option>
              <option value="after_15">15:00 后</option>
              <option value="unknown">不确定</option>
            </select>
          </label>
          <label className="text-ink/65">
            {priceDateLabel}
            <input
              type="date"
              className="mt-1 w-full rounded-[6px] border border-ink/10 bg-white px-3 py-2 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
              value={editConfirmedNavDate}
              onChange={(event) => setEditConfirmedNavDate(event.target.value)}
            />
          </label>
          <label className="text-ink/65">
            {priceLabel}
            <input
              className="mt-1 w-full rounded-[6px] border border-ink/10 bg-white px-3 py-2 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
              inputMode="decimal"
              placeholder="留空表示不改"
              value={editConfirmedNav}
              onChange={(event) => setEditConfirmedNav(event.target.value)}
            />
          </label>
          <label className="text-ink/65">
            {sharesLabel}
            <input
              className="mt-1 w-full rounded-[6px] border border-ink/10 bg-white px-3 py-2 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
              inputMode="decimal"
              placeholder="可选；留空按金额和价格估算"
              value={editConfirmedShares}
              onChange={(event) => setEditConfirmedShares(event.target.value)}
            />
          </label>
          <label className="text-ink/65 sm:col-span-2">
            备注
            <input
              className="mt-1 w-full rounded-[6px] border border-ink/10 bg-white px-3 py-2 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
              placeholder="可选备注"
              value={editNote}
              onChange={(event) => setEditNote(event.target.value)}
            />
          </label>
        </div>
        {updateTracking.isError ? (
          <p className="mt-3 rounded-[8px] bg-rose-50 px-3 py-2 text-xs text-rose-700">
            保存失败：{errorText(updateTracking.error)}
          </p>
        ) : null}
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            className="rounded-[6px] bg-ink px-3 py-2 text-sm font-medium text-white transition hover:bg-ink/90 disabled:opacity-60"
            disabled={updateTracking.isPending}
            onClick={() => updateTracking.mutate()}
          >
            {updateTracking.isPending ? "保存中..." : "保存修改"}
          </button>
          <button
            className="rounded-[6px] border border-border px-3 py-2 text-sm font-medium text-ink transition hover:border-ink/30 disabled:opacity-60"
            disabled={updateTracking.isPending}
            onClick={resetEditTracking}
          >
            取消
          </button>
        </div>
      </div>
    );
  };

  const renderPositionSizingRows = (item: TrackedPosition) => {
    const tradeVerb = ["add", "reentry_candidate"].includes(item.position_action)
      ? "建议买入"
      : ["trim", "reduce", "exit"].includes(item.position_action)
        ? "建议卖出"
        : null;
    const reentryLabels: Record<string, string> = {
      waiting_cooldown: "冷却期内",
      blocked: "暂不适合重新入场",
      candidate: "可重新观察入场",
      not_applicable: "无"
    };
    return (
      <>
        <span>
          当前市值：{item.current_market_value === null ? "等待价格/份额" : formatCurrency(item.current_market_value)}
        </span>
        <span>
          ETF账户占比：{item.current_account_weight === null ? "暂无" : (item.current_account_weight * 100).toFixed(1) + "%"}
        </span>
        <span>
          目标占比：{item.target_account_weight === null ? "暂无" : (item.target_account_weight * 100).toFixed(1) + "%"}
        </span>
        <span>建议动作：{item.recommended_action_label}</span>
        {tradeVerb && item.recommended_trade_amount !== null ? (
          <span>{tradeVerb}：{formatCurrency(item.recommended_trade_amount)}</span>
        ) : null}
        {tradeVerb && item.recommended_trade_shares !== null ? <span>建议份额：约 {item.recommended_trade_shares.toFixed(0)} 份</span> : null}
        {item.reentry_state && item.reentry_state !== "not_applicable" ? (
          <span>重新入场：{reentryLabels[item.reentry_state] ?? item.reentry_state}</span>
        ) : null}
        {item.reentry_reason ? <span>重入说明：{item.reentry_reason}</span> : null}
        {item.position_sizing_reason ? <span>仓位说明：{item.position_sizing_reason}</span> : null}
      </>
    );
  };
  function renderActionLifecyclePanel(item: TrackedPosition) {
    const detail =
      trackedDetail.data?.id === item.id ? trackedDetail.data : null;
    const currentAction = detail?.current_action ?? item.current_action;
    const historyLoaded = detail !== null;
    const actionHistory = detail?.action_history ?? [];
    const lifecycleLabel = currentAction
      ? actionStatusLabel(currentAction.status)
      : trackedLifecycleLabel(item);
    const dataState =
      item.lifecycle_state?.data_state ?? currentAction?.data_state;
    const alertState = item.lifecycle_state?.alert_state;
    const relatedAudits = item.id === primaryTracked?.id ? auditItems : [];
    const mutableStatuses = new Set([
      "proposed",
      "acknowledged",
      "partially_executed"
    ]);
    const activeAction =
      currentAction &&
      currentAction.is_current &&
      mutableStatuses.has(currentAction.status)
        ? currentAction
        : null;
    const actionPending =
      actionTransition.isPending &&
      actionTransition.variables?.actionId === activeAction?.id;

    return (
      <section className="mt-4 rounded-[8px] border border-ink/10 bg-paper p-3 text-sm leading-6 text-ink/70">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-accent">
              动作生命周期
            </p>
            <p className="mt-1 font-semibold text-ink">{lifecycleLabel}</p>
          </div>
          <div className="text-right text-xs text-ink/50">
            <p>告警：{alertStateLabel(alertState)}</p>
            <p>数据：{dataStateLabel(dataState)}</p>
          </div>
        </div>

        {!currentAction && item.exit_signal.action_class === "soft_watch" ? (
          <p className="mt-2 rounded-[6px] bg-white px-3 py-2">
            hold / 仅观察 / 未生成减仓动作
          </p>
        ) : null}
        {!currentAction && lifecycleLabel === "等待数据" ? (
          <p className="mt-2 rounded-[6px] bg-white px-3 py-2">
            当前无法复核，等待合格数据；不会据此生成交易动作。
          </p>
        ) : null}

        {currentAction ? (
          <div className="mt-3 rounded-[8px] bg-white p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="font-semibold text-ink">服务器绝对目标</p>
              <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs font-semibold text-amber-800">
                {actionStatusLabel(currentAction.status)}
              </span>
            </div>
            <p className="mt-1">
              剩余{" "}
              {formatPercent(currentAction.target_remaining_fraction * 100)} ·
              复权归一化目标{" "}
              {currentAction.target_normalized_quantity.toFixed(2)}
            </p>
            <p className="text-xs text-ink/55">
              复权归一化冻结基准{" "}
              {currentAction.baseline_normalized_quantity.toFixed(2)} · 已执行{" "}
              {currentAction.cumulative_executed_quantity.toFixed(2)} · 待执行{" "}
              {currentAction.remaining_execution_quantity.toFixed(2)}
            </p>
            <p className="text-xs text-ink/55">
              券商当前实际份额：
              {(item.confirmed_shares ?? item.estimated_shares)?.toFixed(2) ??
                "等待确认"}
            </p>
            <p className="mt-1 text-xs text-ink/55">
              原因：
              {currentAction.contributing_rules.length
                ? currentAction.contributing_rules
                    .map(alertTypeLabel)
                    .join("、")
                : "等待规则原因"}
            </p>
          </div>
        ) : null}

        {activeAction ? (
          <div className="mt-3 rounded-[8px] border border-ink/10 bg-white p-3">
            <p className="font-semibold text-ink">由持仓所有者确认</p>
            <p className="mt-1 text-xs text-ink/55">
              系统只记录你提供的真实执行事实，不会把邮件或建议当成成交。
            </p>
            <p className="mt-1 text-xs text-ink/55">
              上方目标是复权归一化数量；执行事实请按券商实际份额填写，服务端会校验持仓调整因子。
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              {activeAction.status === "proposed" ? (
                <button
                  className="rounded-[6px] border border-border px-3 py-2 text-xs font-semibold text-ink disabled:opacity-50"
                  disabled={actionPending}
                  onClick={() => {
                    setActionNotice(null);
                    actionTransition.mutate({
                      positionId: item.id,
                      actionId: activeAction.id,
                      expectedPositionStateVersion:
                        detail?.exit_state_version ?? item.exit_state_version,
                      transition: "acknowledge"
                    });
                  }}
                >
                  确认已看到
                </button>
              ) : null}
              <button
                className="rounded-[6px] border border-rose-200 px-3 py-2 text-xs font-semibold text-rose-700 disabled:opacity-50"
                disabled={actionPending}
                onClick={() => {
                  setActionNotice(null);
                  actionTransition.mutate({
                    positionId: item.id,
                    actionId: activeAction.id,
                    expectedPositionStateVersion:
                      detail?.exit_state_version ?? item.exit_state_version,
                    transition: "cancel"
                  });
                }}
              >
                取消建议
              </button>
            </div>

            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <label className="grid gap-1 text-xs font-medium text-ink/70">
                实际执行时间
                <input
                  className="rounded-[6px] border border-border px-2 py-1.5 text-sm text-ink"
                  type="datetime-local"
                  value={actionExecutionDraft(activeAction.id).executedAt}
                  onChange={(event) =>
                    updateActionExecutionDraft(activeAction.id, {
                      executedAt: event.target.value
                    })
                  }
                />
              </label>
              <label className="grid gap-1 text-xs font-medium text-ink/70">
                实际卖出份额
                <input
                  className="rounded-[6px] border border-border px-2 py-1.5 text-sm text-ink"
                  inputMode="decimal"
                  value={actionExecutionDraft(activeAction.id).quantity}
                  onChange={(event) =>
                    updateActionExecutionDraft(activeAction.id, {
                      quantity: event.target.value
                    })
                  }
                />
              </label>
              <label className="grid gap-1 text-xs font-medium text-ink/70">
                实际成交价
                <input
                  className="rounded-[6px] border border-border px-2 py-1.5 text-sm text-ink"
                  inputMode="decimal"
                  value={actionExecutionDraft(activeAction.id).price}
                  onChange={(event) =>
                    updateActionExecutionDraft(activeAction.id, {
                      price: event.target.value
                    })
                  }
                />
              </label>
              <label className="grid gap-1 text-xs font-medium text-ink/70">
                价格来源
                <select
                  className="rounded-[6px] border border-border px-2 py-1.5 text-sm text-ink"
                  value={actionExecutionDraft(activeAction.id).priceSource}
                  onChange={(event) =>
                    updateActionExecutionDraft(activeAction.id, {
                      priceSource: event.target
                        .value as TrackedPositionExecutionPriceSource
                    })
                  }
                >
                  {executionPriceSourceOptions.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="grid gap-1 text-xs font-medium text-ink/70">
                税费
                <input
                  className="rounded-[6px] border border-border px-2 py-1.5 text-sm text-ink"
                  inputMode="decimal"
                  value={actionExecutionDraft(activeAction.id).fees}
                  onChange={(event) =>
                    updateActionExecutionDraft(activeAction.id, {
                      fees: event.target.value
                    })
                  }
                />
              </label>
              <label className="grid gap-1 text-xs font-medium text-ink/70">
                执行后剩余份额
                <input
                  className="rounded-[6px] border border-border px-2 py-1.5 text-sm text-ink"
                  inputMode="decimal"
                  value={actionExecutionDraft(activeAction.id).resultingShares}
                  onChange={(event) =>
                    updateActionExecutionDraft(activeAction.id, {
                      resultingShares: event.target.value
                    })
                  }
                />
              </label>
            </div>
            <label className="mt-2 flex items-center gap-2 text-xs text-ink/65">
              <input
                type="checkbox"
                checked={actionExecutionDraft(activeAction.id).closeFact}
                onChange={(event) =>
                  updateActionExecutionDraft(activeAction.id, {
                    closeFact: event.target.checked
                  })
                }
              />
              已确认持仓完全关闭（仅 0% 目标适用）
            </label>
            <button
              className="mt-3 rounded-[6px] bg-ink px-3 py-2 text-xs font-semibold text-white disabled:opacity-50"
              disabled={actionPending}
              onClick={() => {
                try {
                  setActionNotice(null);
                  actionTransition.mutate({
                    positionId: item.id,
                    actionId: activeAction.id,
                    expectedPositionStateVersion:
                      detail?.exit_state_version ?? item.exit_state_version,
                    transition: "execute",
                    execution: executionFactsFromDraft(activeAction.id)
                  });
                } catch (error) {
                  setActionNotice({
                    actionId: activeAction.id,
                    text: errorText(error)
                  });
                }
              }}
            >
              {actionPending ? "提交中..." : "记录实际执行"}
            </button>
            {actionNotice?.actionId === activeAction.id ? (
              <p className="mt-2 text-xs text-ink/60">{actionNotice.text}</p>
            ) : null}
            {actionTransition.error &&
            actionTransition.variables?.actionId === activeAction.id &&
            actionNotice?.actionId !== activeAction.id ? (
              <p className="mt-2 text-xs text-rose-700">
                操作失败：{errorText(actionTransition.error)}
              </p>
            ) : null}
          </div>
        ) : null}

        {!historyLoaded ? (
          <p className="mt-3 rounded-[6px] bg-white px-3 py-2 text-xs text-ink/55">
            历史与通知尝试未加载；在榜单中选择该标的后查看最近一页记录。
          </p>
        ) : actionHistory.length ? (
          <div className="mt-3">
            <p className="font-semibold text-ink">最近一页动作周期路径</p>
            <p className="text-xs text-ink/50">
              同一动作可以有多个风险原因和多次通知尝试，不会显示成多笔卖出决定。
            </p>
            {detail?.action_history_next_cursor ||
            trackedAudit.data?.next_cursor ? (
              <p className="mt-1 text-xs text-amber-700">
                还有更早记录，当前未加载。
              </p>
            ) : null}
            <div className="mt-2 grid gap-2">
              {actionHistory.map((action) => {
                const attempts = relatedAudits.filter(
                  (audit) =>
                    audit.correlation?.action_decision_id === action.id &&
                    audit.alert_type === "notification_delivery"
                );
                return (
                  <div key={action.id} className="rounded-[8px] bg-white p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span className="font-semibold text-ink">
                        {actionStatusLabel(action.status)}
                      </span>
                      <span className="text-xs text-ink/45">
                        目标剩余{" "}
                        {formatPercent(action.target_remaining_fraction * 100)}
                      </span>
                    </div>
                    <p className="mt-1 text-xs text-ink/55">
                      {action.contributing_rules.length
                        ? action.contributing_rules
                            .map(alertTypeLabel)
                            .join(" + ")
                        : "未记录附加规则原因"}
                    </p>
                    <p className="mt-1 text-xs text-ink/55">
                      进度：已执行{" "}
                      {action.cumulative_executed_quantity.toFixed(2)}，剩余{" "}
                      {action.remaining_execution_quantity.toFixed(2)}
                    </p>
                    {attempts.length ? (
                      <div className="mt-2 border-l-2 border-ink/10 pl-3 text-xs text-ink/55">
                        {attempts.map((attempt) => (
                          <p
                            key={`${attempt.id}-${attempt.correlation?.notification_envelope_id ?? "none"}`}
                          >
                            {formatDateTime(attempt.created_at)} ·{" "}
                            {notificationDeliveryLabel(
                              attempt.smtp_result ?? attempt.outcome
                            )}
                            {recordNumber(
                              attempt.decision_context,
                              "attempt_count"
                            )
                              ? ` · 第 ${recordNumber(attempt.decision_context, "attempt_count")} 次尝试`
                              : ""}
                          </p>
                        ))}
                      </div>
                    ) : (
                      <p className="mt-2 text-xs text-ink/45">
                        当前已加载范围内暂无关联通知尝试。
                      </p>
                    )}
                    <details className="mt-2 text-xs text-ink/50">
                      <summary className="cursor-pointer font-semibold text-ink/65">
                        技术标识与审计上下文
                      </summary>
                      <div className="mt-1 grid gap-1 break-all font-mono">
                        <span>action_id: {action.id}</span>
                        <span>policy_version: {action.policy_version}</span>
                        <span>
                          execution_provenance: {action.execution_provenance}
                        </span>
                        {attempts.map((attempt) => (
                          <span key={`technical-${attempt.id}`}>
                            event: {attempt.correlation?.event_id ?? attempt.id}{" "}
                            · item:{" "}
                            {attempt.correlation?.notification_item_id ?? "-"} ·
                            envelope:{" "}
                            {attempt.correlation?.notification_envelope_id ??
                              "-"}
                          </span>
                        ))}
                      </div>
                      {attempts[0] ? (
                        <pre className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-[6px] bg-paper p-2">
                          {JSON.stringify(
                            {
                              threshold: attempts[0].threshold_context,
                              decision: attempts[0].decision_context
                            },
                            null,
                            2
                          )}
                        </pre>
                      ) : null}
                    </details>
                  </div>
                );
              })}
            </div>
            <p className="mt-2 text-xs text-ink/50">
              SMTP
              已接受只表示服务器接收。外部邮件可能重复，但不会重复生成仓位动作。
            </p>
          </div>
        ) : null}
      </section>
    );
  }
  const renderMobileTrackingPanel = () => (
    <Panel className="rounded-[12px]">
      {activeTracked.length ? (
        <div className="grid gap-3">
          {activeTracked.map((item) => (
            <div key={item.id} className="rounded-[10px] border border-ink/10 bg-white p-4">
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
                {renderPositionSizingRows(item)}
                <span>
                  持有天数: {item.holding_days === null ? "暂无" : `${item.holding_days} 天`}
                </span>
                <span>
                  当前价: {item.current_snapshot.current_price?.toFixed(4) ?? "暂无"}
                  {item.asset_type === "etf"
                    ? "（" + priceSourceLabel(item.current_snapshot.price_source) + (item.intraday_snapshot?.quote_time ? "，" + formatDateTime(item.intraday_snapshot.quote_time) : "") + "）"
                    : ""}
                </span>
                {item.asset_type === "etf" ? (
                  <span>数据口径: {reliabilityLabel(item.intraday_snapshot?.reliability_level)}</span>
                ) : null}
                {item.asset_type === "etf" ? <span>{snapshotReliabilityLine(item.intraday_snapshot)}</span> : null}
                <span>
                  成本口径: {item.cost_basis === null ? "等待成本数据" : `${formatCurrency(item.cost_basis)} / ${costBasisSourceLabel(item.cost_basis_source)}`}
                </span>
                <span>持仓处理状态: {item.exit_signal.label}</span>
                <span>{item.exit_signal.email_eligible ? "满足邮件提醒条件" : "不会发邮件"}</span>
              </div>
              {item.latest_alert ? (
                <p className="mt-3 rounded-[12px] bg-paper px-3 py-2 text-xs text-ink/65">
                  最新预警: {alertTypeLabel(item.latest_alert.alert_type)} / {alertDeliveryLabel(item.latest_alert)}
                </p>
              ) : (
                <p className="mt-3 rounded-[12px] bg-paper px-3 py-2 text-xs leading-5 text-ink/65">
                  暂无追踪告警。
                </p>
              )}
              {renderActionLifecyclePanel(item)}

            <div className="mt-4 flex flex-wrap gap-2">
                <button
                  className="rounded-[6px] border border-border px-3 py-2 text-sm font-medium text-ink transition hover:border-ink/30 hover:text-ink disabled:opacity-60"
                  disabled={updateTracking.isPending}
                  onClick={() => openEditTracking(item)}
                >
                  编辑买入信息
                </button>
                <button
                  className="rounded-[6px] border border-border px-3 py-2 text-sm font-medium text-ink transition hover:border-ink/30 hover:text-ink disabled:opacity-60"
                  disabled={closeTracking.isPending}
                  onClick={() => closeTracking.mutate(item.id)}
                >
                  停止追踪
                </button>
              </div>
              {renderTrackingEditForm(item)}
            </div>
          ))}
        </div>
      ) : (
        <div className="rounded-[10px] border border-dashed border-ink/20 bg-white p-5 text-sm leading-7 text-ink/55">
          {mode.trackingEmpty}
        </div>
      )}
    </Panel>
  );

  const renderDesktopTrackingPanel = () => (
    <div ref={holdingsSectionRef} className="scroll-mt-28">
      <Panel className="rounded-[12px]">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
        <SectionKicker eyebrow="我的持仓观察" title={mode.trackingTitle} description={mode.trackingDescription} />
        <div className="rounded-[10px] bg-paper px-4 py-3 text-sm leading-6 text-ink/65">
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
              <span>
                当前价：{item.current_snapshot.current_price?.toFixed(4) ?? "暂无"}
                {item.asset_type === "etf"
                  ? "（" + priceSourceLabel(item.current_snapshot.price_source) + (item.intraday_snapshot?.quote_time ? "，" + formatDateTime(item.intraday_snapshot.quote_time) : "") + "）"
                  : ""}
              </span>
              <span>
                展示口径：{priceSourceLabel(item.current_snapshot.price_source)} / {reliabilityLabel(item.current_snapshot.data_reliability)}
                {item.current_snapshot.decision_eligible ? "，可用于提醒判断" : "，仅展示/估算参考"}
              </span>
              {item.current_snapshot.display_only_reason ? <span>说明：{item.current_snapshot.display_only_reason}</span> : null}
              {item.asset_type === "etf" ? (
                <>
                  <span>
                    数据口径：{reliabilityLabel(item.intraday_snapshot?.reliability_level)}
                    {item.intraday_snapshot?.email_eligible ? "，可用于盘中提醒" : "，仅网页/估算参考"}
                  </span>
                  {item.asset_type === "etf" ? <span>{snapshotReliabilityLine(item.intraday_snapshot)}</span> : null}
                </>
              ) : null}
              <span>
                成本口径：{item.cost_basis === null ? "等待成本数据" : `${formatCurrency(item.cost_basis)} / ${costBasisSourceLabel(item.cost_basis_source)}`}
              </span>
              <span>持有：{item.holding_days === null ? "等待数据" : `${item.holding_days} 天`}</span>
              <span>{item.confirmed_shares === null ? "估算份额" : "确认份额"}：{item.estimated_shares === null ? "等待价格" : item.estimated_shares.toFixed(2)}</span>
              <span className={pnlTone(item.current_snapshot.estimated_pnl)}>估算盈亏：{pnlText(item)}</span>
              {renderPositionSizingRows(item)}
            </div>

            <div className={`mt-4 rounded-[8px] p-3 text-sm leading-6 ${exitSignalTone(item.exit_signal.level)}`}>
              <p className="text-xs font-semibold opacity-75">持仓处理状态</p>
              <p className="font-semibold">{item.exit_signal.label}</p>
              <p className="mt-1 text-xs opacity-75">
                {item.exit_signal.action_class === "actionable_exit"
                  ? "行动信号"
                  : item.exit_signal.action_class === "soft_watch"
                    ? "软提醒"
                    : item.exit_signal.action_class === "guard_only"
                      ? "风险警戒，仅网页展示"
                      : item.exit_signal.action_class === "data_waiting"
                        ? "等待数据"
                        : "观察状态"}
                {item.exit_signal.guard_state ? ` · ${item.exit_signal.guard_state}` : ""}
              </p>
              <p className="mt-1">{item.exit_signal.reason ?? "暂无持仓处理原因，继续观察公开数据。"}</p>
              {item.exit_signal.guard_reasons.length > 0 ? (
                <p className="mt-1 text-xs opacity-75">{item.exit_signal.guard_reasons[0]}</p>
              ) : null}
              {item.exit_signal.no_alert_reason ? (
                <p className="mt-1 text-xs opacity-75">未发提醒原因：{item.exit_signal.no_alert_reason}</p>
              ) : null}
              <p className="mt-1 text-xs opacity-75">
                {item.exit_signal.email_eligible ? "满足邮件提醒条件" : "不会发邮件"}
                {item.exit_signal.email_eligibility_reason ? `：${item.exit_signal.email_eligibility_reason}` : ""}
              </p>
            </div>

            {item.latest_alert ? (
              <div className={`mt-4 rounded-[8px] p-3 text-sm leading-6 ${latestAlertTone(item.latest_alert)}`}>
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
              <p className="mt-4 rounded-[8px] bg-paper p-3 text-sm leading-6 text-ink/55">
                暂无可展示告警。
              </p>
            )}

            {renderActionLifecyclePanel(item)}

            <details className="mt-3 rounded-[8px] bg-paper p-3 text-xs leading-5 text-ink/60">
              <summary className="cursor-pointer font-semibold text-ink">更多风控数据</summary>
              <div className="mt-2 grid gap-1">
                <span>最高盈利：{percentOrWaiting(item.max_profit_pct)}</span>
                <span>高点回吐：{percentOrWaiting(item.profit_giveback_pct)}</span>
                {item.asset_type === "etf" ? (
                  <>
                    <span>价格来源：{priceSourceLabel(item.intraday_snapshot?.price_source)}</span>
                    <span>可靠性：{reliabilityLabel(item.intraday_snapshot?.reliability_level)}</span>
                    <span>行情时间：{formatDateTime(item.intraday_snapshot?.quote_time)}</span>
                    <span>{item.intraday_snapshot?.message ?? "暂无数据口径说明。"}</span>
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
                    <span>阈值说明：{thresholdExplanationLine(item)}</span>
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

            <div className="mt-4 flex flex-wrap gap-2">
              <button
                className="rounded-[6px] border border-border px-3 py-2 text-sm font-medium text-ink transition hover:border-ink/30 hover:text-ink disabled:opacity-60"
                disabled={updateTracking.isPending}
                onClick={() => openEditTracking(item)}
              >
                编辑买入信息
              </button>
              <button
                className="rounded-[6px] border border-border px-3 py-2 text-sm font-medium text-ink transition hover:border-ink/30 hover:text-ink disabled:opacity-60"
                disabled={closeTracking.isPending}
                onClick={() => closeTracking.mutate(item.id)}
              >
                标记已卖出 / 停止提醒
              </button>
            </div>
            {renderTrackingEditForm(item)}
          </div>
        ))}
        {!trackedPositions.isLoading && activeTracked.length === 0 ? (
          <div className="rounded-[10px] border border-dashed border-ink/20 bg-white p-5 text-sm leading-7 text-ink/55 xl:col-span-3">
            {mode.trackingEmpty}
          </div>
        ) : null}
      </div>
      </Panel>
    </div>
  );

  const renderMobileExplanationPanel = () => (
    <Panel className="rounded-[12px]">
      {selectedAsset ? (
        <div className="space-y-4">
          <p className="font-semibold text-ink">{selectedAsset.name} - 低频说明</p>
          <div className="rounded-[10px] bg-paper p-4">
            <p className="text-xs uppercase tracking-[0.18em] text-accent">图表</p>
            <div className="mt-3 h-52">
              {detailPoints.length ? (
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={detailPoints}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e6e6e6" />
                    <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={28} />
                    <YAxis tickLine={false} axisLine={false} width={56} domain={["dataMin", "dataMax"]} />
                    <Tooltip formatter={(value) => Number(value).toFixed(4)} />
                    <Area
                      type="monotone"
                      dataKey="value"
                      name={assetType === "etf" ? "净值" : "净值"}
                      stroke="#107d32"
                      fill="#dce9df"
                    />
                  </AreaChart>
                </ResponsiveContainer>
              ) : (
                <div className="flex h-full items-center justify-center rounded-[8px] bg-white text-sm text-ink/50">暂无图表数据</div>
              )}
            </div>
          </div>
          <div className="rounded-[10px] bg-paper p-4">
            <p className="font-semibold text-ink">AI/规则说明</p>
            <p className="mt-2 text-sm leading-6 text-ink/65">
              {advisorReport ? `${advisorReport.plain_summary}` : "暂无完整解释结果。"}
            </p>
          </div>
          <div className="rounded-[10px] border border-ink/10 bg-white p-4">
            <p className="font-semibold text-ink">标签原因</p>
            <p className="mt-2 text-sm leading-7 text-ink/65">
              {selectedAsset.conclusion === "数据不足" ? labelMeaning("数据不足") : rationaleText(selectedAsset, "label_meaning", labelMeaning(selectedAsset.conclusion))}
            </p>
          </div>
          <div className="rounded-[10px] border border-ink/10 bg-white p-4">
            <p className="font-semibold text-ink">观察组合</p>
            <p className="mt-2 text-sm leading-7 text-ink/65">
              {assetType === "etf" ? observationPortfolio.data?.note ?? "暂无观察组合说明" : "非 ETF 模式下暂不显示观察组合"}
            </p>
          </div>
          {dataIssues.length ? (
            <div className="rounded-[10px] border border-ink/10 bg-white p-4">
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
        </div>
      ) : (
        <div className="rounded-[10px] border border-dashed border-ink/20 p-6 text-sm leading-6 text-ink/55">
          先在“榜单”选择标的后查看说明信息
        </div>
      )}
    </Panel>
  );

  return (
    <div className="space-y-6">
      <div className="hidden lg:block">
        {renderDesktopTrackingPanel()}
      </div>

      <Panel className="rounded-[12px] bg-white">
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_auto] xl:items-start">
          <SectionKicker eyebrow="短线研究工作台" title={mode.title} description={mode.description} />
          <div className="flex flex-wrap gap-2 xl:justify-end">
            <TaskButton disabled={isResearchTaskPending} onClick={() => syncData.mutate()}>
              {syncData.isPending ? "准备数据中" : mode.dataButton}
            </TaskButton>
            <TaskButton variant="primary" disabled={isResearchTaskPending} onClick={() => runSignals.mutate()}>
              {runSignals.isPending
                ? (assetType === "etf" ? "刷新中" : "排序生成中")
                : (assetType === "etf" ? "刷新综合排名" : "生成短线排序")}
            </TaskButton>
            <TaskButton disabled={isResearchTaskPending || advisorSourceRunId === null} onClick={() => runAdvisor.mutate()}>
              {runAdvisor.isPending ? "报告生成中" : "AI 研究说明"}
            </TaskButton>
          </div>
        </div>

        <div className="mt-5 flex flex-wrap gap-2 rounded-[10px] bg-blush p-1.5">
          {(["etf", "fund"] as AssetType[]).map((item) => {
            const isActive = assetType === item;
            return (
              <button
                key={item}
                className={`rounded-[6px] px-3 py-2 text-sm font-medium transition ${
                  isActive ? "bg-ink text-white shadow-sm" : "bg-white text-ink hover:text-accent"
                }`}
                onClick={() => {
                  setAssetType(item);
                  setSort(item === "etf" ? "opportunity" : "score");
                  setTheme("all");
                  setSelected(null);
                  setAssetOffset(0);
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
          <WorkbenchMetric label="当前列表" value={`${visibleAssets.length}/${totalAssetCount} 只`} tone="bg-accentSoft text-ink" />
          <WorkbenchMetric label={mode.latestLabel} value={formatDate(currentLatestDate)} />
          {assetType === "etf" ? (
            <WorkbenchMetric label="盘中行情时间" value={formatDateTime(latestIntradayQuoteTime)} tone="bg-white text-ink" />
          ) : null}
          <WorkbenchMetric label="短线观察" value={`${observableCount} 只`} tone="bg-emerald-50 text-emerald-800" />
          <WorkbenchMetric label="高位观察" value={`${highRiskCount} 只`} tone="bg-rose-50 text-rose-800" />
          {assetType === "etf" ? (
            <WorkbenchMetric label="标签验证" value={`${validationGroups.length} 组`} tone="bg-white text-ink" />
          ) : null}
          {assetType === "etf" ? (
            <WorkbenchMetric label="验证时间" value={formatUtcDateTime(statusData?.label_validation_generated_at)} tone="bg-white text-ink" />
          ) : null}
          {assetType === "etf" ? (
            <WorkbenchMetric label="TopN验证" value={`${scoreBucketGroups.length} 组`} tone="bg-white text-ink" />
          ) : null}
          {assetType === "etf" ? (
            <WorkbenchMetric label="TopN时间" value={formatUtcDateTime(statusData?.score_bucket_validation_generated_at)} tone="bg-white text-ink" />
          ) : null}
          {assetType === "etf" ? (
            <WorkbenchMetric label="日线缺口/失败" value={`${statusData?.etf_data_stale_count ?? 0} / ${statusData?.etf_failed_count ?? 0} 只`} tone="bg-amber-50 text-amber-900" />
          ) : null}
          {assetType !== "etf" ? (
            <WorkbenchMetric label="数据异常" value={`${currentDataIssueCount} 只`} tone="bg-amber-50 text-amber-900" />
          ) : null}
        </div>

        {assetType === "etf" ? (
          <div className="mt-4 grid gap-3 rounded-[10px] border border-ink/10 bg-ink px-4 py-3 text-white md:grid-cols-5">
            <div>
              <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-white/50">盘中盯盘</p>
              <p className="mt-1 text-sm text-white/75">评分前 20 + 短线/高位观察 + 已追踪 ETF</p>
            </div>
            <div>
              <p className="text-xs text-white/50">市场状态</p>
              <p className="font-semibold">{marketStatusLabel(etfLiveStatus?.market_status)}</p>
            </div>
            <div>
              <p className="text-xs text-white/50">盯盘数量</p>
              <p className="font-semibold">{etfLiveStatus?.watched_count ?? 0} 只</p>
            </div>
            <div>
              <p className="text-xs text-white/50">盘中行情</p>
              <p className="font-semibold">{formatDateTime(latestIntradayQuoteTime)}</p>
            </div>
            <div>
              <p className="text-xs text-white/50">最近运行</p>
              <p className="font-semibold">{formatUtcDateTime(etfLiveStatus?.latest_run?.finished_at)}</p>
            </div>
          </div>
        ) : null}

        {lastResult ? (
          <details className="mt-4 rounded-[10px] bg-paper px-4 py-3 text-sm text-ink/65">
            <summary className="cursor-pointer font-semibold text-ink">查看最近一次任务结果</summary>
            <pre className="mt-3 max-h-40 overflow-auto rounded-[8px] bg-white p-3 text-xs leading-5">
              {safeSummary(lastResult)}
            </pre>
          </details>
        ) : null}
      </Panel>

      {(syncData.isError || runSignals.isError || runAdvisor.isError || status.isError || assets.isError) && (
        <p className="rounded-[10px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
          {errorText(syncData.error ?? runSignals.error ?? runAdvisor.error ?? status.error ?? assets.error)}
        </p>
      )}

      {assetType === "etf" ? (
        <Panel className="rounded-[12px] bg-white">
          <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
            <div>
              <p className="text-sm font-semibold text-accent">综合关注分层验证</p>
              <h2 className="mt-1 text-xl font-semibold text-ink">Top 5/10/20/50 的日期级净收益验证</h2>
              <p className="mt-2 text-sm leading-6 text-ink/60">
                预设主终点为 Top 10 五日相对 all_scored 的配对净超额；其他组合仅作探索性展示，不参与实时排序、持仓提醒或邮件。
              </p>
            </div>
            <div className="grid gap-2 text-sm sm:grid-cols-3">
              <StatPill label="来源 run" value={`${scoreBucketSourceRuns}`} tone="bg-paper text-ink" />
              <StatPill label="可评分样本" value={`${scoreBucketScoredItems}`} tone="bg-paper text-ink" />
              <StatPill label="排除缺失分" value={`${scoreBucketExcludedItems}`} tone="bg-paper text-ink" />
            </div>
          </div>
          {scoreBucketGroups.length ? (
            <div className="mt-4 space-y-4">
              <p className="rounded-[8px] bg-paper px-3 py-2 text-sm leading-6 text-ink/70">
                {scoreBucketPrimaryEndpointText(scoreBucketPrimaryGroup)}
              </p>
              <div className="overflow-x-auto rounded-[8px] border border-ink/10">
                <table className="min-w-full divide-y divide-ink/10 text-left text-sm">
                  <thead className="bg-paper text-xs text-ink/55">
                    <tr>
                      <th className="px-3 py-2 font-semibold">累计组</th>
                      <th className="px-3 py-2 font-semibold">5日中位/胜率</th>
                      <th className="px-3 py-2 font-semibold">10日中位/胜率</th>
                      <th className="px-3 py-2 font-semibold">样本/入选</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-ink/10">
                    {scoreBucketCumulativeGroups.map((group) => (
                      <tr key={`${group.label}-${group.entry_timing_label}`} className="bg-white">
                        <td className="px-3 py-2 font-semibold text-ink">{group.label}</td>
                        <td className="px-3 py-2 text-ink/65">{scoreBucketWindowText(group, "5")}</td>
                        <td className="px-3 py-2 text-ink/65">{scoreBucketWindowText(group, "10")}</td>
                        <td className="px-3 py-2 text-ink/65">{scoreBucketSampleText(group)}</td>
                      </tr>
                    ))}
                    {scoreBucketBaselineGroup ? (
                      <tr className="bg-paper/60">
                        <td className="px-3 py-2 font-semibold text-ink">all_scored</td>
                        <td className="px-3 py-2 text-ink/65">{scoreBucketWindowText(scoreBucketBaselineGroup, "5")}</td>
                        <td className="px-3 py-2 text-ink/65">{scoreBucketWindowText(scoreBucketBaselineGroup, "10")}</td>
                        <td className="px-3 py-2 text-ink/65">{scoreBucketSampleText(scoreBucketBaselineGroup)}</td>
                      </tr>
                    ) : null}
                  </tbody>
                </table>
              </div>
              {scoreBucketMarginalGroups.length ? (
                <div className="overflow-x-auto rounded-[8px] border border-ink/10">
                  <table className="min-w-full divide-y divide-ink/10 text-left text-sm">
                    <thead className="bg-paper text-xs text-ink/55">
                      <tr>
                        <th className="px-3 py-2 font-semibold">边际组</th>
                        <th className="px-3 py-2 font-semibold">5日中位/胜率</th>
                        <th className="px-3 py-2 font-semibold">10日中位/胜率</th>
                        <th className="px-3 py-2 font-semibold">样本/入选</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-ink/10">
                      {scoreBucketMarginalGroups.map((group) => (
                        <tr key={`${group.label}-${group.entry_timing_label}`} className="bg-white">
                          <td className="px-3 py-2 font-semibold text-ink">{group.label}</td>
                          <td className="px-3 py-2 text-ink/65">{scoreBucketWindowText(group, "5")}</td>
                          <td className="px-3 py-2 text-ink/65">{scoreBucketWindowText(group, "10")}</td>
                          <td className="px-3 py-2 text-ink/65">{scoreBucketSampleText(group)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : null}
              <p className="text-xs leading-5 text-ink/50">
                窗口：{formatDate(String(scoreBucketValidation.replay_start_date ?? ""))} - {formatDate(String(scoreBucketValidation.replay_end_date ?? ""))}；
                排序字段：{String(scoreBucketValidation.score_version ?? "final_score_v3")} / {String(scoreBucketValidation.score_field ?? "ranking_score")}；
                执行：{String(scoreBucketValidation.execution_model ?? "等待执行模型")}；缺失真实综合关注分、复权价格或契约的 ETF 已排除。
              </p>
              {scoreBucketExcludedSources.length ? (
                <details className="rounded-[8px] border border-amber-200 bg-amber-50 px-3 py-2 text-xs leading-5 text-amber-900">
                  <summary className="cursor-pointer font-semibold">旧口径或不兼容来源已排除，不计入当前结论（{scoreBucketExcludedSources.length} 个）</summary>
                  <ul className="mt-2 list-disc pl-4">
                    {scoreBucketExcludedSources.slice(0, 6).map((item) => (
                      <li key={`${item.source_signal_run_id}-${item.reason}`}>
                        run {item.source_signal_run_id ?? "-"} / {item.source_date ?? "-"}：{item.reason ?? "不兼容"}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
            </div>
          ) : (
            <div className="mt-4 rounded-[8px] border border-dashed border-ink/20 bg-paper px-4 py-5 text-sm text-ink/55">
              {scoreBucketUnavailableText(scoreBucketValidation)}
            </div>
          )}
        </Panel>
      ) : null}

      <div className="lg:hidden">
        <div className="rounded-[10px] bg-white p-1">
          <div className="grid grid-cols-4 gap-2">
            {mobileTabs.map((item) => (
              <button
                key={item.id}
                className={`rounded-[6px] px-3 py-2 text-sm font-medium transition ${
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
            <Panel className="rounded-[12px]">{rankingPanelContent({ compact: true })}</Panel>
          ) : null}
          {mobileTab === "detail" ? renderMobileDetailPanel() : null}
          {mobileTab === "tracking" ? renderMobileTrackingPanel() : null}
          {mobileTab === "explanation" ? renderMobileExplanationPanel() : null}
        </div>
      </div>

      <div className="hidden gap-6 lg:grid lg:grid-cols-[minmax(360px,0.76fr)_minmax(0,1.24fr)] lg:items-start xl:grid-cols-[minmax(380px,0.72fr)_minmax(0,1.28fr)]">
        <Panel className="rounded-[12px] lg:sticky lg:top-24 lg:max-h-[calc(100vh-7rem)] lg:overflow-y-auto">
          <div className="-mx-5 -mt-5 border-b border-border bg-white px-5 pb-4 pt-5">
            <div className="mb-5 flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
              <SectionKicker
                eyebrow="买入观察榜单"
                title={`${mode.shortLabel}排序`}
              />
              <span className="w-fit rounded-full bg-blush px-2.5 py-1 text-xs font-semibold text-ink/60">
                每页 12 只
              </span>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <div className="rounded-[6px] border border-border bg-white px-3 py-2 text-sm font-medium text-ink">
                {mode.classification}
              </div>
              <input
                className="rounded-[6px] border border-border bg-white px-3 py-2 text-sm outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/15"
                placeholder="搜基金名或代码"
                value={keyword}
                onChange={(event) => setKeyword(event.target.value)}
              />
              <select
                className="rounded-[6px] border border-border bg-white px-3 py-2 text-sm outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/15"
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
                className="rounded-[6px] border border-border bg-white px-3 py-2 text-sm outline-none transition focus:border-accent focus:ring-2 focus:ring-accent/15"
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
            {assetType === "etf" ? labelFilterPanel : null}

            <div className="mt-5">
              <AssetPaginationBar
                total={totalAssetCount}
                offset={assetOffset}
                visibleCount={visibleAssets.length}
                isFetching={assets.isFetching}
                scopeLabel={assetCountScope}
                onPrevious={goToPreviousAssetPage}
                onNext={goToNextAssetPage}
              />
            </div>
          </div>

          <div className="mt-5 min-h-[520px] space-y-3 pr-1 lg:pb-1">
            {assets.isLoading ? (
              <div className="rounded-[10px] border border-dashed border-ink/20 p-6 text-sm text-ink/55">
                正在读取短线研究池...
              </div>
            ) : null}
            {assets.isError ? (
              <div className="rounded-[10px] border border-rose-200 bg-rose-50 p-6 text-sm leading-6 text-rose-800">
                榜单请求失败：{errorText(assets.error)}
                <button type="button" className="ml-2 font-semibold underline underline-offset-2" onClick={() => void assets.refetch()}>
                  重试
                </button>
              </div>
            ) : null}
            {visibleAssets.map((item) => {
              const itemAssetType = getItemAssetType(item);
              const itemCode = toEtfItemCode(item);
              const isSelected = selected?.asset_type === itemAssetType && selected.code === itemCode;
              const isLiveItem = isLiveRankingItem(item);
              const quote = liveRankingQuote(item);
              const livePrice = quote?.latest_price;
              const timingDisplay = itemEntryTimingDisplay(item, etfLiveData?.market_status);
              const liveChange = intradayChangeDisplay(quote, etfLiveData?.market_status);
              return (
                <button
                  key={`${itemAssetType}-${itemCode}`}
                  className={`w-full rounded-[10px] border p-4 text-left transition ${
                    isSelected ? "border-ink bg-ink text-white" : "border-ink/10 bg-white text-ink hover:border-accent"
                  }`}
                  onClick={() => selectAsset(item)}
                >
                  <div className="flex flex-col gap-3">
                    <div className="min-w-0">
                      <p className={`text-xs font-semibold ${isSelected ? "text-white/60" : "text-accent"}`}>
                        {isLiveItem ? "实时排名" : `#${item.rank ?? "-"}`}
                        {isLiveItem ? "" : ` · ${assetTypeLabel(item.asset_type)}`}
                        {!isLiveItem ? ` · ${shortResearchThemeTags(item).slice(0, 3).join(" / ")}` : ""}
                      </p>
                      <h3 className="mt-2 text-xl font-semibold leading-tight">
                        {isLiveItem ? toEtfItemName(item) : item.name}
                        <span className={`ml-2 text-sm font-normal ${isSelected ? "text-white/45" : "text-ink/45"}`}>
                          {isLiveItem ? itemCode : item.code}
                        </span>
                      </h3>
                      <p className={`mt-2 line-clamp-2 text-sm leading-6 ${isSelected ? "text-white/65" : "text-ink/60"}`}>
                        {timingDisplay.reason}
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white text-ink" : "bg-ink text-white"}`}>
                        {isLiveItem
                          ? liveScoreText(item)
                          : assetScoreText(item)}
                      </span>
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${
                        isSelected ? "bg-white/15 text-white" : conclusionTone(itemConclusion(item))
                      }`}>
                        买入观察状态：{itemConclusion(item)}
                      </span>
                      <span
                        className={`rounded-full px-3 py-1 text-xs font-semibold ${
                          isSelected ? "bg-white/15 text-white" : entryTimingTone(timingDisplay.label)
                        }`}
                      >
                        {timingDisplay.title}：{timingDisplay.label}
                      </span>
                    {isLiveItem ? (
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white/15 text-white" : ""}`}>
                        实时排名变化：{etfLiveRankChangeText(item.rank_change)}
                      </span>
                    ) : item.advisor_report ? (
                      <span className={`rounded-full px-3 py-1 text-xs font-semibold ${isSelected ? "bg-white/15 text-white" : advisorTone(item.advisor_report.action_label)}`}>
                        {item.advisor_report.action_label}
                      </span>
                    ) : null}
                    </div>
                  </div>
                  <div className={`mt-4 grid gap-2 text-sm sm:grid-cols-2 ${isSelected ? "text-white/75" : "text-ink/65"}`}>
                    {isLiveItem ? (
                      <>
                        <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                          全局排名 #{etfGlobalRank(item) ?? "-"}
                        </span>
                        <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                          当前范围排名 #{toEtfItemRank(item) ?? "-"} · 筛选位置 #{etfFilteredPosition(item) ?? "-"}
                        </span>
                        <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                          当前价：{quote ? (livePrice === null || livePrice === undefined ? "暂无" : livePrice.toFixed(4)) : "暂无"}
                        </span>
                        <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                          盘中涨跌：{liveChange.value}
                        </span>
                        <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                          行情：{liveChange.time}
                        </span>
                        <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                          排名变化：{etfLiveRankChangeText(item.rank_change)}
                        </span>
                      </>
                    ) : (
                      <>
                        <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                          最新日涨跌：{percentMetric(item.metrics, "today_return_pct")}
                        </span>
                        <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                          近 20 日：{percentMetric(item.metrics, "return_20d")}
                        </span>
                        <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                          60 日回撤：{percentMetric(item.metrics, "max_drawdown_60d")}
                        </span>
                        <span className={`rounded-[12px] px-3 py-2 ${isSelected ? "bg-white/10" : "bg-paper"}`}>
                          样本：{item.usable_days} 天
                        </span>
                      </>
                    )}
                  </div>
                </button>
              );
            })}
            {!assets.isLoading && rawAssets.length > 0 && visibleAssets.length === 0 ? (
              <div className="rounded-[10px] border border-dashed border-ink/20 bg-white p-6 text-sm leading-6 text-ink/55">
                当前页没有匹配这些标签的 ETF。可以清空标签筛选、换一页，或调整方向/搜索条件。
                <button
                  type="button"
                  className="ml-2 font-semibold text-accent underline underline-offset-2"
                  onClick={() => setQuickLabelFilter("clear")}
                >
                  清空筛选
                </button>
              </div>
            ) : null}
            {!assets.isLoading && !assets.isError && rawAssets.length === 0 ? (
              <div className="rounded-[10px] border border-dashed border-ink/20 p-6 text-sm leading-6 text-ink/55">
                {mode.noResults}
              </div>
            ) : null}
            <AssetPaginationBar
              total={totalAssetCount}
              offset={assetOffset}
              visibleCount={visibleAssets.length}
              isFetching={assets.isFetching}
              scopeLabel={assetCountScope}
              onPrevious={goToPreviousAssetPage}
              onNext={goToNextAssetPage}
            />
          </div>
        </Panel>

        <div className="min-w-0 space-y-6">
          <Panel className="rounded-[12px]">
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
                    {selectedTracked.length ? (
                      <p className="mt-2 text-sm text-emerald-700">你正在追踪这只资产的 {selectedTracked.length} 笔买入。</p>
                    ) : null}
                    {isSelectedDetailPending ? (
                      <p className="mt-2 inline-flex rounded-full bg-blush px-2.5 py-1 text-xs font-semibold text-ink/60 transition-opacity duration-150">
                        正在更新 {selectedAsset.code} 详情...
                      </p>
                    ) : null}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <span className="rounded-full bg-ink px-4 py-2 text-sm font-semibold text-white">
                      {assetScoreSummaryText(selectedAsset)}
                    </span>
                    {selectedAsset.asset_type === "etf" && selectedAsset.opportunity_score !== null && selectedAsset.opportunity_score !== undefined ? (
                      <span className="rounded-full bg-accent px-4 py-2 text-sm font-semibold text-white">
                        {opportunityScoreText(selectedAsset)}
                      </span>
                    ) : null}
                    <span className="rounded-full bg-paper px-4 py-2 text-sm font-semibold text-ink/70">
                      {scoreVersionText(selectedAsset)}
                    </span>
                    <span className={`rounded-full px-4 py-2 text-sm font-semibold ${conclusionTone(selectedAsset.conclusion)}`}>
                      买入观察：{selectedAsset.conclusion}
                    </span>
                    <span className={`rounded-full px-4 py-2 text-sm font-semibold ${entryTimingTone(selectedEntryTiming.label)}`}>
                      {assetType === "etf" ? "盘中买点" : "今日买点"}：{selectedEntryTiming.label}
                    </span>
                  </div>
                </div>

                <div className="mt-4 flex flex-wrap gap-2 rounded-[10px] bg-blush p-1.5">
                  {detailNavItems.map((item) => (
                    <button
                      key={item.section}
                      className="rounded-[6px] bg-white px-3 py-2 text-sm font-medium text-ink transition hover:bg-ink hover:text-white"
                      onClick={() => scrollToDetailSection(item.section)}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>

                <div ref={summarySectionRef} className="scroll-mt-28">
                  <div className="mt-5 grid gap-3 md:grid-cols-[1fr_0.82fr]">
                    <div className="rounded-[10px] bg-paper p-4">
                      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">当前结论</p>
                      <p className="mt-2 text-lg font-semibold text-ink">
                        {selectedAsset.conclusion} · {assetScoreSummaryText(selectedAsset)}
                      </p>
                      <p className="mt-2 text-sm leading-7 text-ink/65">
                        {rationaleText(selectedAsset, "key_reason", "按近期趋势、回撤、波动、成交额和数据质量综合生成。")}
                      </p>
                    </div>
                    <div className="rounded-[10px] bg-ink p-4 text-white">
                      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-white/50">买点口径</p>
                      <p className="mt-2 text-sm leading-7 text-white/75">
                        {assetType === "etf"
                          ? `短线观察 = 值得看；盘中买点以实时行情为准，日线买点只做参考。${selectedEntryTiming.reason}`
                          : `短线观察 = 值得看；今日买点状态 = 今天更适合追、等、还是回避。${selectedEntryTiming.reason}`}
                      </p>
                    </div>
                  </div>

                  {selectedAsset.asset_type === "etf" ? (
                    <div className="mt-3 rounded-[10px] border border-accent/20 bg-white p-4">
                      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">研究榜与行动资格</p>
                      <p className="mt-2 text-sm leading-6 text-ink/65">
                        研究榜 #{selectedAsset.research_rank ?? "-"} · {formatOptionalScore(selectedAsset.research_score)} 分
                        {"；"}{historyTierText(selectedAsset.history_confidence_tier)}
                      </p>
                      <p className="mt-1 text-sm leading-6 text-ink/65">{actionableStateText(selectedAsset)}</p>
                      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                        <p className="mt-4 text-xs font-semibold uppercase tracking-[0.18em] text-accent">主题/板块辅助</p>
                        <p className="text-sm font-semibold text-ink">
                          {opportunityStatusText(selectedAsset)}
                        </p>
                      </div>
                      <p className="mt-1 text-xs text-ink/50">{opportunityVersionText(selectedAsset)}</p>
                      <p className="mt-1 text-xs text-ink/50">{factorProfileText(selectedAsset)}</p>
                      <p className="mt-1 text-xs text-ink/50">{activeRiskGateText(selectedAsset)}</p>
                      <p className="mt-2 text-sm leading-6 text-ink/65">
                        板块趋势：{selectedAsset.sector_trend_summary || sectorTrendText(selectedAsset)}
                      </p>
                      <div className="mt-3 grid gap-2 sm:grid-cols-3">
                        <StatPill label="技术分" value={formatOptionalScore(selectedAsset.technical_score)} tone="bg-paper text-ink" />
                        <StatPill label="因子综合" value={formatOptionalScore(selectedAsset.factor_profile_score)} tone="bg-paper text-ink" />
                        <StatPill label="板块趋势" value={formatOptionalScore(selectedAsset.sector_trend_score)} tone="bg-paper text-ink" />
                      </div>
                      {factorGroupHighlights(selectedAsset).length ? (
                        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                          {factorGroupHighlights(selectedAsset).map((item) => (
                            <div key={item.key} className="rounded-[8px] border border-ink/10 bg-paper px-3 py-2">
                              <p className="text-xs text-ink/45">{item.label}</p>
                              <p className={`mt-1 text-sm font-semibold ${item.muted ? "text-ink/45" : "text-ink"}`}>{item.value}</p>
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  ) : null}

                  {selectedAsset.asset_type === "etf" ? (
                    <div className="mt-3">
                      <CatalystShadowPanel asset={selectedAsset} />
                    </div>
                  ) : null}

                  <div className="mt-3 rounded-[10px] border border-ink/10 bg-white p-4">
                    <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">评分拆解</p>
                    </div>
                    <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-5">
                      {scoreDimensionLabels(selectedAsset).map((item) => (
                        <div key={item.key} className="rounded-[8px] border border-ink/10 bg-paper px-3 py-2">
                          <p className="text-xs text-ink/45">{item.label}</p>
                          <p className="mt-1 text-base font-semibold text-ink">{scoreDimensionValue(selectedAsset, item.key)}</p>
                        </div>
                      ))}
                    </div>
                  </div>

                  {renderAssetStatusSummary()}
                </div>

                <div ref={trackingSectionRef} className="mt-5 scroll-mt-28 rounded-[10px] border border-ink/10 bg-white p-4">
                  <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                    <div>
                      <p className="text-sm font-semibold text-ink">我已买入，开始追踪</p>
                    </div>
                    <button
                      className="rounded-[6px] bg-accent px-4 py-2.5 text-sm font-medium text-white transition hover:bg-accent/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
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
                          className="mt-2 w-full rounded-[6px] border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
                          inputMode="decimal"
                          value={trackingAmount}
                          onChange={(event) => setTrackingAmount(event.target.value)}
                        />
                      </label>
                      <label className="text-sm text-ink/65">
                        下单日期
                        <input
                          className="mt-2 w-full rounded-[6px] border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
                          type="date"
                          value={trackingDate}
                          onChange={(event) => setTrackingDate(event.target.value)}
                        />
                      </label>
                      <label className="text-sm text-ink/65">
                        下单时间
                        <select
                          className="mt-2 w-full rounded-[6px] border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
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
                          className="mt-2 w-full rounded-[6px] border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
                          type="date"
                          value={trackingConfirmedNavDate}
                          onChange={(event) => setTrackingConfirmedNavDate(event.target.value)}
                        />
                      </label>
                      <label className="text-sm text-ink/65">
                        {assetType === "etf" ? "实际成交价（可选）" : "支付宝确认净值（可选）"}
                        <input
                          className="mt-2 w-full rounded-[6px] border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
                          inputMode="decimal"
                          placeholder={assetType === "etf" ? "例如：1.238" : "例如：7.3130"}
                          value={trackingConfirmedNav}
                          onChange={(event) => setTrackingConfirmedNav(event.target.value)}
                        />
                      </label>
                      <label className="text-sm text-ink/65">
                        {assetType === "etf" ? "实际成交份额（可选）" : "支付宝确认份额（可选）"}
                        <input
                          className="mt-2 w-full rounded-[6px] border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
                          inputMode="decimal"
                          placeholder="确认份额最准确"
                          value={trackingConfirmedShares}
                          onChange={(event) => setTrackingConfirmedShares(event.target.value)}
                        />
                      </label>
                      <label className="text-sm text-ink/65">
                        备注
                        <input
                          className="mt-2 w-full rounded-[6px] border border-ink/10 bg-paper px-4 py-3 text-ink outline-none focus:border-accent focus:ring-2 focus:ring-accent/15"
                          placeholder={assetType === "etf" ? "例如：证券账户手动买入" : "例如：支付宝手动买入"}
                          value={trackingNote}
                          onChange={(event) => setTrackingNote(event.target.value)}
                        />
                      </label>
                      <button
                        className="self-end rounded-[6px] bg-ink px-4 py-2.5 text-sm font-medium text-white transition hover:bg-ink/90 disabled:opacity-60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                        disabled={createTracking.isPending || Number(trackingAmount) <= 0}
                        onClick={() => createTracking.mutate()}
                      >
                        {createTracking.isPending ? "保存中..." : "保存追踪"}
                      </button>
                    </div>
                  ) : null}
                  {createTracking.isError ? (
                    <p className="mt-3 rounded-[8px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
                      创建追踪失败：{errorText(createTracking.error)}
                    </p>
                  ) : null}
                </div>

                {primaryTracked ? (
                  <div className="mt-5 rounded-[10px] border border-ink/10 bg-white p-4">
                    <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                      <div>
                        <p className="text-sm font-semibold text-ink">追踪收益保护图</p>
                      </div>
                      <span className={`w-fit rounded-full px-3 py-1 text-xs font-semibold ${exitSignalTone(primaryTracked.exit_signal.level)}`}>
                        {primaryTracked.exit_signal.label}
                      </span>
                    </div>
                    <div className="mt-4 h-64">
                      {trackingPoints.length ? (
                        <ResponsiveContainer width="100%" height="100%">
                          <LineChart data={trackingPoints}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#e6e6e6" />
                            <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={28} />
                            <YAxis tickFormatter={(value) => `${Number(value).toFixed(0)}%`} tickLine={false} axisLine={false} width={56} />
                            <Tooltip formatter={(value) => (value === null ? "暂无" : `${Number(value).toFixed(2)}%`)} />
                            <Line type="monotone" dataKey="pnl" name="估算收益" stroke="#107d32" strokeWidth={2} dot={false} connectNulls />
                            <Line
                              type="monotone"
                              dataKey="stop"
                              name="移动止盈线"
                              stroke="#006bff"
                              strokeDasharray="5 5"
                              strokeWidth={2}
                              dot={false}
                              connectNulls
                            />
                          </LineChart>
                        </ResponsiveContainer>
                      ) : (
                        <div className="flex h-full items-center justify-center rounded-[10px] bg-paper text-sm text-ink/50">
                          暂无追踪曲线，等待公开数据更新。
                        </div>
                      )}
                    </div>
                    <div className="mt-3 grid gap-2 text-sm text-ink/65 md:grid-cols-3">
                      <span>确认点：{trackingEntry ? `${trackingEntry.label} / ${percentOrWaiting(trackingEntry.pnl)}` : "等待数据"}</span>
                      <span>最高点：{trackingHigh ? `${trackingHigh.label} / ${percentOrWaiting(trackingHigh.pnl)}` : "等待数据"}</span>
                      <span>当前点：{trackingCurrent ? `${trackingCurrent.label} / ${percentOrWaiting(trackingCurrent.pnl)}` : "等待数据"}</span>
                    </div>
                    <p className="mt-3 rounded-[8px] bg-paper px-4 py-3 text-sm leading-6 text-ink/65">
                      {primaryTracked.exit_signal.reason ?? "暂无持仓处理原因，继续观察公开数据。"}
                    </p>
                  </div>
                ) : null}

                <div className={`mt-5 grid gap-3 ${assetType === "etf" ? "md:grid-cols-3 xl:grid-cols-6" : "md:grid-cols-5"}`}>
                  <StatPill label={mode.latestLabel} value={formatDate(selectedAsset.latest_date)} tone="bg-white text-ink" />
                  <StatPill label={mode.priceLabel} value={selectedAsset.latest_value === null ? "暂无" : selectedAsset.latest_value.toFixed(4)} />
                  {assetType === "etf" ? (
                    <StatPill label="盘中价" value={selectedLiveQuote?.latest_price === undefined ? "暂无" : selectedLiveQuote.latest_price.toFixed(4)} tone="bg-white text-ink" />
                  ) : null}
                  {assetType === "etf" ? (
                    <StatPill label="盘中涨跌" value={selectedIntradayChange.value} tone="bg-white text-ink" />
                  ) : null}
                  {assetType === "etf" ? (
                    <StatPill label="行情时间" value={selectedIntradayChange.time} tone="bg-white text-ink" />
                  ) : null}
                  <StatPill label="可用样本" value={`${selectedAsset.usable_days} 天`} tone="bg-accentSoft text-ink" />
                  <StatPill label={assetType === "etf" ? "盘中买点" : "今日买点"} value={selectedEntryTiming.label} tone="bg-white text-ink" />
                  {assetType === "etf" ? (
                    <StatPill
                      label="20 日成交额"
                      value={formatTurnover(numericMetric(selectedAsset.metrics, "average_turnover_20d"))}
                      tone="bg-white text-ink"
                    />
                  ) : null}
                  <StatPill label="60 日回撤" value={percentMetric(selectedAsset.metrics, "max_drawdown_60d")} tone="bg-white text-ink" />
                </div>

                {assetType === "etf" ? (
                  <p className="mt-4 rounded-[8px] bg-paper px-4 py-3 text-sm leading-6 text-ink/65">{quoteReliabilityLine(selectedLiveQuote)}</p>
                ) : null}

                <div ref={advisorSectionRef} className="mt-6 scroll-mt-28 rounded-[10px] border border-ink/10 bg-white p-5">
                  <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                    <div>
                      <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">今日研究建议</p>
                      <h3 className="mt-2 text-xl font-semibold text-ink">
                        {advisorReport
                          ? advisorReport.plain_summary
                          : isSelectedDetailPending
                            ? `正在更新 ${selectedAsset.code} 研究说明...`
                            : "还没有 AI 研究报告，先看规则解释。"}
                      </h3>
                      {advisorReport ? (
                        <p className="mt-2 text-sm leading-6 text-ink/60">
                          {advisorSourceLabel(advisorReport.source)} · {advisorReport.model_name} · {formatDate(advisorReport.generated_at)}
                        </p>
                      ) : null}
                    </div>
                    <span className={`w-fit rounded-full px-4 py-2 text-sm font-semibold ${advisorTone(advisorReport?.action_label ?? "暂不考虑")}`}>
                      {advisorReport?.action_label ?? "暂无报告"}
                    </span>
                  </div>
                  {advisorReport ? (
                    <div className="mt-5 grid gap-4 md:grid-cols-2">
                      <div className="rounded-[10px] bg-paper p-4">
                        <p className="font-semibold text-ink">为什么</p>
                        <ul className="mt-2 space-y-2 text-sm leading-6 text-ink/65">
                          {advisorReport.opportunity.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      </div>
                      <div className="rounded-[10px] bg-paper p-4">
                        <p className="font-semibold text-ink">风险和反方</p>
                        <ul className="mt-2 space-y-2 text-sm leading-6 text-ink/65">
                          {advisorReport.risks.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                        <p className="mt-3 text-sm leading-6 text-ink/65">{advisorReport.opposing_view}</p>
                      </div>
                      <div className="rounded-[10px] bg-paper p-4">
                        <p className="font-semibold text-ink">接下来观察</p>
                        <ul className="mt-2 space-y-2 text-sm leading-6 text-ink/65">
                          {advisorReport.watch_conditions.map((item) => (
                            <li key={item}>{item}</li>
                          ))}
                        </ul>
                      </div>
                      <div className="rounded-[10px] bg-paper p-4">
                        <p className="font-semibold text-ink">持有和数据限制</p>
                        <p className="mt-2 text-sm leading-6 text-ink/65">{advisorReport.holding_note}</p>
                        <p className="mt-3 text-sm leading-6 text-ink/65">{advisorReport.data_limitations}</p>
                      </div>
                    </div>
                  ) : null}
                </div>

                <div ref={chartsSectionRef} className="mt-6 grid scroll-mt-28 gap-5 xl:grid-cols-2">
                  <div className="rounded-[10px] bg-paper p-4">
                    <p className="font-semibold text-ink">走势</p>
                    <div className="mt-4 h-72">
                      {detailPoints.length ? (
                        <ResponsiveContainer width="100%" height="100%">
                          <AreaChart data={detailPoints}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#e6e6e6" />
                            <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={28} />
                            <YAxis tickLine={false} axisLine={false} width={56} domain={["dataMin", "dataMax"]} />
                            <Tooltip formatter={(value) => Number(value).toFixed(4)} />
                            <Area type="monotone" dataKey="value" name={assetType === "etf" ? "收盘价" : "净值"} stroke="#107d32" fill="#dce9df" />
                          </AreaChart>
                        </ResponsiveContainer>
                      ) : isSelectedDetailPending ? (
                        <DetailLoadingPlaceholder text={`正在更新 ${selectedAsset.code} 走势图...`} />
                      ) : (
                        <div className="flex h-full items-center justify-center rounded-[10px] bg-white text-sm text-ink/50">
                          暂无走势图。先准备数据后再查看。
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="rounded-[10px] bg-paper p-4">
                    <p className="font-semibold text-ink">从高点回落</p>
                    <div className="mt-4 h-72">
                      {detailPoints.length ? (
                        <ResponsiveContainer width="100%" height="100%">
                          <AreaChart data={detailPoints}>
                            <CartesianGrid strokeDasharray="3 3" stroke="#e6e6e6" />
                            <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={28} />
                            <YAxis tickFormatter={(value) => `${Number(value).toFixed(0)}%`} tickLine={false} axisLine={false} width={56} />
                            <Tooltip formatter={(value) => `${Number(value).toFixed(2)}%`} />
                            <Area type="monotone" dataKey="drawdown" name="回落幅度" stroke="#006bff" fill="#f1d8ca" />
                          </AreaChart>
                        </ResponsiveContainer>
                      ) : isSelectedDetailPending ? (
                        <DetailLoadingPlaceholder text={`正在更新 ${selectedAsset.code} 回撤图...`} />
                      ) : (
                        <div className="flex h-full items-center justify-center rounded-[10px] bg-white text-sm text-ink/50">
                          暂无回撤图。
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="rounded-[10px] bg-paper p-4">
                    <p className="font-semibold text-ink">近期涨跌窗口</p>
                    <div className="mt-4 h-64">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={windowPoints}>
                          <CartesianGrid stroke="#e6e6e6" vertical={false} />
                          <XAxis dataKey="label" tickLine={false} axisLine={false} />
                          <YAxis tickFormatter={(value) => `${Number(value).toFixed(0)}%`} tickLine={false} axisLine={false} width={56} />
                          <Tooltip formatter={(value) => (value === null ? "暂无" : `${Number(value).toFixed(2)}%`)} />
                          <Bar dataKey="percent" name="涨跌幅" fill="#107d32" radius={[8, 8, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>

                  {assetType === "etf" ? (
                    <div className="rounded-[10px] bg-paper p-4">
                      <p className="font-semibold text-ink">成交额</p>
                      <div className="mt-4 h-64">
                        {detailPoints.some((point) => point.turnover !== null) ? (
                          <ResponsiveContainer width="100%" height="100%">
                            <BarChart data={detailPoints}>
                              <CartesianGrid stroke="#e6e6e6" vertical={false} />
                              <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={28} />
                              <YAxis tickFormatter={(value) => `${Number(value).toFixed(1)}亿`} tickLine={false} axisLine={false} width={56} />
                              <Tooltip formatter={(value) => (value === null ? "暂无" : `${Number(value).toFixed(2)} 亿元`)} />
                              <Bar dataKey="turnover" name="成交额" fill="#e3b873" radius={[8, 8, 0, 0]} />
                            </BarChart>
                          </ResponsiveContainer>
                        ) : isSelectedDetailPending ? (
                          <DetailLoadingPlaceholder text={`正在更新 ${selectedAsset.code} 成交额...`} />
                        ) : (
                          <div className="flex h-full items-center justify-center rounded-[10px] bg-white text-sm text-ink/50">
                            暂无成交额数据。
                          </div>
                        )}
                      </div>
                    </div>
                  ) : null}

                  <div className="rounded-[10px] bg-paper p-4">
                    <p className="font-semibold text-ink">样本和风险说明</p>
                    <div className="mt-4 h-64">
                      <div className="flex h-full flex-col justify-center rounded-[10px] bg-white p-5 text-sm leading-7 text-ink/60">
                        <p>{selectedAsset.sample_level}</p>
                        <p className="mt-2">
                          风险标签：{selectedAsset.risk_flags.length ? selectedAsset.risk_flags.join("、") : "暂未触发主要风险标签"}
                        </p>
                        <p className="mt-2">数据来源：{selectedAsset.source_note}</p>
                      </div>
                    </div>
                  </div>
                </div>
              </>
            ) : (
              <div className="rounded-[10px] border border-dashed border-ink/20 p-8 text-sm leading-6 text-ink/55">
                {mode.detailEmpty}
              </div>
            )}
          </Panel>

          {selectedAsset ? (
            <div ref={explanationSectionRef} className="scroll-mt-28">
              <Panel className="rounded-[12px]">
                <p className="text-lg font-semibold text-ink">分析说明</p>
                <div className="mt-4 grid gap-4 md:grid-cols-2">
                  {isSelectedDetailPending ? (
                    <div className="rounded-[10px] border border-ink/10 bg-white p-4 text-sm leading-7 text-ink/60">
                      正在更新 {selectedAsset.code} 的完整分析说明...
                    </div>
                  ) : null}
                  {Object.entries(selectedDetailForCurrent?.explanation_sections ?? {}).map(([title, text]) => (
                    <div key={title} className="rounded-[10px] border border-ink/10 bg-white p-4">
                      <p className="font-semibold text-ink">{title}</p>
                      <p className="mt-2 text-sm leading-7 text-ink/65">{text}</p>
                    </div>
                  ))}
                  <div className="rounded-[10px] border border-ink/10 bg-white p-4">
                    <p className="font-semibold text-ink">标签原因</p>
                    <p className="mt-2 text-sm leading-7 text-ink/65">
                      {rationaleText(selectedAsset, "label_meaning", labelMeaning(selectedAsset.conclusion))}
                    </p>
                  </div>
                  <div className="rounded-[10px] border border-ink/10 bg-white p-4">
                    <p className="font-semibold text-ink">风险标签</p>
                    <p className="mt-2 text-sm leading-7 text-ink/65">
                      {selectedAsset.risk_flags.length ? selectedAsset.risk_flags.join("、") : "暂未触发主要风险标签。"}
                    </p>
                  </div>
                </div>
              </Panel>
            </div>
          ) : null}
        </div>
      </div>

      <div className="hidden lg:block">


      {assetType === "etf" ? (
        <Panel className="rounded-[12px] bg-white/70">
          <details>
            <summary className="cursor-pointer text-lg font-semibold text-ink">
              ETF 资金配置参考
              <span className="ml-3 rounded-full bg-accentSoft/60 px-3 py-1 text-xs text-ink">
                权重合计 {observationPortfolio.data ? formatPercent((observationPortfolio.data.weight_sum ?? 0) * 100) : "暂无"}
              </span>
              <span className="ml-2 rounded-full bg-white px-3 py-1 text-xs text-ink/60">
                {observationPortfolio.data?.portfolio_mode === "defensive"
                  ? "防守优先"
                  : observationPortfolio.data?.portfolio_mode === "cash_wait"
                    ? "等待现金"
                    : observationPortfolio.data?.portfolio_mode === "neutral"
                      ? "混合配置"
                      : "进攻配置"}
              </span>
            </summary>
            <p className="mt-3 rounded-[8px] bg-paper px-4 py-3 text-xs leading-5 text-ink/55">
              {observationPortfolio.data?.methodology ?? "按买点、风险和数据可靠性筛选，再做分散约束。"}
              {observationPortfolio.data
                ? ` 单只 ETF 上限 ${formatPercent((observationPortfolio.data.single_weight_cap ?? 0) * 100)}，ETF 资金最高投入 ${formatPercent((observationPortfolio.data.target_invested_weight ?? 1) * 100)}。`
                : ""}
            </p>
            <div className="mt-3 rounded-[8px] border border-ink/10 bg-white px-4 py-3 text-xs leading-5 text-ink/60">
              <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between">
                <p className="font-semibold text-ink">组合证据契约</p>
                <span className="w-fit rounded-full bg-paper px-2.5 py-1 text-[11px] font-semibold text-ink/65">
                  {observationPortfolio.data?.evidence_status ?? "等待验证"}
                </span>
              </div>
              <p className="mt-2">
                {evidenceContractText(observationPortfolio.data?.evidence_status, observationPortfolio.data?.evidence_summary)}
              </p>
            </div>
            <div className="mt-3 rounded-[10px] border border-ink/10 bg-white px-4 py-3">
              <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                <div>
                  <p className="text-sm font-semibold text-ink">优化组合对照</p>
                </div>
                <button
                  type="button"
                  className="w-fit rounded-[6px] border border-ink bg-ink px-3 py-2 text-xs font-semibold text-white transition hover:bg-ink/85 disabled:opacity-60"
                  disabled={isResearchTaskPending}
                  onClick={() => runEtfOptimizedAllocation.mutate()}
                >
                  {runEtfOptimizedAllocation.isPending ? "正在优化..." : "运行优化对照"}
                </button>
              </div>
              {runEtfOptimizedAllocation.isError ? (
                <p className="mt-3 rounded-[8px] border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-700">
                  {errorText(runEtfOptimizedAllocation.error)}
                </p>
              ) : null}
              {optimizedAllocationData?.methods.length ? (
                <div className="mt-3 grid gap-3 lg:grid-cols-3">
                  {optimizedAllocationData.methods.map((method) => (
                    <div key={method.method} className="rounded-[8px] border border-border bg-paper/40 p-3">
                      <div className="flex items-start justify-between gap-3">
                        <p className="text-sm font-semibold text-ink">{method.label}</p>
                        <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-ink/55">
                          {formatPercent(method.weight_sum * 100)}
                        </span>
                      </div>
                      <p className="mt-2 text-xs leading-5 text-ink/55">
                        {method.unavailable_reason ??
                          (method.method === "black_litterman"
                            ? "按市场先验、结构化标签 view、证据置信度和约束生成的 Black-Litterman 对照。"
                            : "按历史波动、单只上限和主题集中度约束生成的数学权重对照。")}
                      </p>
                      {method.method === "black_litterman" ? (
                        <div className="mt-3 grid gap-1 rounded-[8px] bg-white px-3 py-2 text-[11px] leading-5 text-ink/55">
                          <p>先验来源：{String(method.summary.prior_source ?? "等待数据")}</p>
                          <p>
                            结构化 view：{String(method.summary.view_count ?? 0)} 个；平均置信度：
                            {recordNumber(method.summary.confidence_summary, "avg") === null
                              ? "暂无"
                              : formatPercent((recordNumber(method.summary.confidence_summary, "avg") ?? 0) * 100)}
                          </p>
                          <p>
                            约束：单只上限 {formatPercent(Number(method.summary.constraints?.single_weight_cap ?? 0.3) * 100)}；
                            主题上限 {formatPercent(Number(method.summary.constraints?.theme_exposure_cap ?? 0.6) * 100)}
                          </p>
                          <p>排除资产：{String(method.summary.excluded_count ?? 0)} 只；仅作研究对照，需要人工判断。</p>
                        </div>
                      ) : null}
                      <div className="mt-3 space-y-2">
                        {method.items.slice(0, 4).map((item) => (
                          <div key={item.code} className="flex items-center justify-between gap-3 text-xs text-ink/60">
                            <span className="truncate">{item.name}</span>
                            <span className="font-semibold text-ink">{formatPercent(item.target_weight * 100)}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="mt-3 rounded-[8px] border border-dashed border-ink/20 bg-paper px-3 py-3 text-xs leading-5 text-ink/55">
                  {optimizedAllocationData?.unavailable_reason ?? "暂无优化组合对照。候选数量、历史数据或主题分散度不足时不会硬凑数学权重。"}
                </p>
              )}
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-4">
              <div className="rounded-[10px] border border-ink/10 bg-white px-4 py-3">
                <p className="text-xs text-ink/45">主配置</p>
                <p className="mt-1 text-lg font-semibold text-ink">
                  {formatPercent((observationPortfolio.data?.primary_weight ?? observationPortfolio.data?.risk_exposure_weight ?? 0) * 100)}
                </p>
              </div>
              <div className="rounded-[10px] border border-ink/10 bg-white px-4 py-3">
                <p className="text-xs text-ink/45">小仓观察</p>
                <p className="mt-1 text-lg font-semibold text-ink">
                  {formatPercent((observationPortfolio.data?.satellite_weight ?? 0) * 100)}
                </p>
              </div>
              <div className="rounded-[10px] border border-ink/10 bg-white px-4 py-3">
                <p className="text-xs text-ink/45">防守仓位</p>
                <p className="mt-1 text-lg font-semibold text-ink">
                  {formatPercent((observationPortfolio.data?.defensive_weight ?? 0) * 100)}
                </p>
              </div>
              <div className="rounded-[10px] border border-ink/10 bg-white px-4 py-3">
                <p className="text-xs text-ink/45">等待资金</p>
                <p className="mt-1 text-lg font-semibold text-ink">
                  {formatPercent((observationPortfolio.data?.cash_weight ?? 0) * 100)}
                </p>
              </div>
            </div>
            {observationPortfolio.data?.portfolio_mode === "defensive" ? (
              <p className="mt-4 rounded-[10px] border border-sky-200 bg-sky-50 px-4 py-3 text-sm text-sky-800">
                当前市场不适合全仓进攻，优先使用防守 ETF 做资金配置参考。
              </p>
            ) : null}
            {(observationPortfolio.data?.cash_weight ?? 0) > 0 && observationPortfolio.data?.portfolio_mode !== "cash_wait" ? (
              <p className="mt-4 rounded-[10px] border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                {observationPortfolio.data?.cash_reason ?? "合格 ETF 不足以用满资金，剩余资金等待，不硬凑标的。"}
              </p>
            ) : null}
            {observationPortfolio.data?.portfolio_mode === "cash_wait" ? (
              <p className="mt-4 rounded-[10px] border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                {observationPortfolio.data.cash_reason ?? observationPortfolio.data.unavailable_reason ?? "当前没有满足条件的进攻/防守 ETF，建议等待，不给买入权重。"}
              </p>
            ) : observationPortfolio.data?.unavailable_reason ? (
              <p className="mt-4 rounded-[10px] border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                {observationPortfolio.data.unavailable_reason}
              </p>
            ) : null}
            <div className="mt-5 flex items-center justify-between gap-3">
              <p className="text-sm font-semibold text-ink">主配置</p>
              <span className="rounded-full bg-ink px-3 py-1 text-xs font-semibold text-white">
                {observationPortfolio.data?.items.length ?? 0} 只
              </span>
            </div>
            <div className="mt-3 grid gap-3 lg:grid-cols-3">
              {(observationPortfolio.data?.items ?? []).map((item) => (
                <div key={item.code} className="rounded-[10px] border border-ink/10 bg-white p-4">
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
                  <p className="mt-3 rounded-[8px] bg-paper px-3 py-2 text-xs leading-5 text-ink/55">
                    {item.weight_explanation ?? `风险：${item.risk_reasons.join("；")}`}
                  </p>
                </div>
              ))}
            </div>
            {!observationPortfolio.isLoading && !(observationPortfolio.data?.items ?? []).length ? (
              <p className="mt-4 rounded-[10px] bg-paper px-4 py-3 text-sm text-ink/55">
                暂无进攻仓位。当前高分 ETF 可能偏高位、买点不合适、候选不足，或数据不足。
              </p>
            ) : null}
            {(observationPortfolio.data?.satellite_items ?? []).length ? (
              <>
                <div className="mt-6 flex items-center justify-between gap-3">
                  <p className="text-sm font-semibold text-ink">小仓观察</p>
                  <span className="rounded-full bg-amber-50 px-3 py-1 text-xs font-semibold text-amber-700">
                    {observationPortfolio.data?.satellite_items?.length ?? 0} 只
                  </span>
                </div>
                <p className="mt-2 text-xs leading-5 text-ink/55">
                  小仓观察通常来自高位但买点尚未明显转弱的 ETF，权重上限更低，只作观察参考。
                </p>
                <div className="mt-3 grid gap-3 lg:grid-cols-3">
                  {(observationPortfolio.data?.satellite_items ?? []).map((item) => (
                    <div key={`satellite-${item.code}`} className="rounded-[10px] border border-amber-100 bg-amber-50/50 p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-ink">{item.name}</p>
                          <p className="mt-1 text-xs text-ink/45">{item.code} · {formatDate(item.data_date)}</p>
                        </div>
                        <span className="rounded-full bg-white px-3 py-1 text-xs font-semibold text-amber-700">
                          {formatPercent(item.target_weight * 100)}
                        </span>
                      </div>
                      <p className="mt-3 text-sm text-ink/65">综合分 {item.score.toFixed(1)} · {item.conclusion}</p>
                      <p className="mt-3 rounded-[8px] bg-white/70 px-3 py-2 text-xs leading-5 text-ink/55">
                        {item.weight_explanation ?? `小仓原因：${item.risk_reasons.slice(0, 3).join("；")}`}
                      </p>
                    </div>
                  ))}
                </div>
              </>
            ) : null}
            {(observationPortfolio.data?.defensive_items ?? []).length ? (
              <>
                <div className="mt-6 flex items-center justify-between gap-3">
                  <p className="text-sm font-semibold text-ink">防守仓位</p>
                  <span className="rounded-full bg-sky-50 px-3 py-1 text-xs font-semibold text-sky-700">
                    {observationPortfolio.data?.defensive_items?.length ?? 0} 只
                  </span>
                </div>
                <div className="mt-3 grid gap-3 lg:grid-cols-3">
                  {(observationPortfolio.data?.defensive_items ?? []).map((item) => (
                    <div key={`defensive-${item.code}`} className="rounded-[10px] border border-sky-100 bg-sky-50/50 p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-ink">{item.name}</p>
                          <p className="mt-1 text-xs text-ink/45">{item.code} · {formatDate(item.data_date)}</p>
                        </div>
                        <span className="rounded-full bg-white px-3 py-1 text-xs font-semibold text-sky-700">
                          {formatPercent(item.target_weight * 100)}
                        </span>
                      </div>
                      <p className="mt-3 text-sm text-ink/65">综合分 {item.score.toFixed(1)} · {item.conclusion}</p>
                      <p className="mt-3 rounded-[8px] bg-white/70 px-3 py-2 text-xs leading-5 text-ink/55">
                        {item.weight_explanation ?? `防守原因：${item.risk_reasons.slice(0, 3).join("；")}`}
                      </p>
                    </div>
                  ))}
                </div>
              </>
            ) : null}
            {(observationPortfolio.data?.watch_only_items ?? []).length ? (
              <>
                <div className="mt-6 flex items-center justify-between gap-3">
                  <p className="text-sm font-semibold text-ink">强势但别追</p>
                  <span className="rounded-full bg-rose-50 px-3 py-1 text-xs font-semibold text-rose-700">
                    {observationPortfolio.data?.watch_only_items.length ?? 0} 只
                  </span>
                </div>
                <div className="mt-3 grid gap-3 lg:grid-cols-3">
                  {(observationPortfolio.data?.watch_only_items ?? []).map((item) => (
                    <div key={`watch-${item.code}`} className="rounded-[10px] border border-rose-100 bg-rose-50/40 p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <p className="text-sm font-semibold text-ink">{item.name}</p>
                          <p className="mt-1 text-xs text-ink/45">{item.code} · {formatDate(item.data_date)}</p>
                        </div>
                        <span className="rounded-full bg-white px-3 py-1 text-xs font-semibold text-rose-700">
                          不给权重
                        </span>
                      </div>
                      <p className="mt-3 text-sm text-ink/65">综合分 {item.score.toFixed(1)} · {item.conclusion}</p>
                      <p className="mt-3 rounded-[8px] bg-white/70 px-3 py-2 text-xs leading-5 text-ink/55">
                        {item.exclusion_explanation ?? `原因：${item.risk_reasons.slice(0, 3).join("；")}`}
                      </p>
                    </div>
                  ))}
                </div>
              </>
            ) : null}
            {(observationPortfolio.data?.excluded_items ?? []).length ? (
              <>
                <div className="mt-6 flex items-center justify-between gap-3">
                  <p className="text-sm font-semibold text-ink">等待 / 回避</p>
                  <span className="rounded-full bg-blush px-2.5 py-1 text-xs font-semibold text-ink/55">
                    {observationPortfolio.data?.excluded_items.length ?? 0} 只
                  </span>
                </div>
                <div className="mt-3 grid gap-3 lg:grid-cols-3">
                  {(observationPortfolio.data?.excluded_items ?? []).map((item) => (
                    <div key={`excluded-${item.code}`} className="rounded-[10px] border border-ink/10 bg-white/60 p-4">
                      <p className="text-sm font-semibold text-ink">
                        {item.name} <span className="text-ink/40">{item.code}</span>
                      </p>
                      <p className="mt-2 text-xs leading-5 text-ink/55">
                        {item.exclusion_explanation ?? item.risk_reasons.slice(0, 3).join("；")}
                      </p>
                    </div>
                  ))}
                </div>
              </>
            ) : null}
          </details>
        </Panel>
      ) : null}

      {dataIssues.length ? (
        <Panel className="rounded-[12px]">
          <p className="text-lg font-semibold text-ink">需要留意的数据问题</p>
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {dataIssues.map((item) => (
              <div key={`${item.asset_type}-${item.code}`} className="rounded-[10px] border border-ink/10 bg-white p-4 text-sm leading-6">
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

      </div>
    </div>
  );
}
export default function ShortTermPage() {
  return (
    <Suspense fallback={<div className="text-sm text-ink/60">正在加载短线研究...</div>}>
      <ShortTermClient />
    </Suspense>
  );
}
