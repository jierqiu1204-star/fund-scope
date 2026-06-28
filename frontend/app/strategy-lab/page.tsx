"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

import { Panel, SectionHeader, StatPill } from "@/components/ui";
import { api } from "@/lib/api";
import { formatCurrency, formatDate, formatPercent } from "@/lib/format";
import type {
  DataStatus,
  EtfUniverseResponse,
  LatestRecommendationsWithReviewResponse,
  PaperPortfolio,
  PaperReconciliation,
  RecommendationReview,
  RecommendationReviewItem,
  EtfDataStatus,
  ShortEtfPaper,
  ShortEtfEvaluation,
  ShortEtfEvaluationItem,
  ShortEtfReview,
  ShortEtfReviewItem,
  ShortEtfSignalItem,
  ShortEtfSignalRun,
  StrategyDefinition,
  StrategyEvaluation,
  StrategyEvaluationItem,
  StrategyRun,
  StrategyType
} from "@/lib/types";

type ViewKey = "strategies" | "backtests" | "evaluation" | "paper" | "screening" | "short-etf";

type DataAction = {
  key: string;
  label: string;
  endpoint: string;
};

const views: Array<{ key: ViewKey; label: string }> = [
  { key: "strategies", label: "策略" },
  { key: "backtests", label: "回测" },
  { key: "evaluation", label: "可靠性评估" },
  { key: "paper", label: "模拟盘" },
  { key: "screening", label: "筛选评分" }
];

const dataActions: DataAction[] = [
  { key: "daily_fund_nav", label: "拉取最近净值", endpoint: "/api/admin/jobs/daily_fund_nav/run" },
  { key: "fund_nav_backfill_180", label: "回填 180 天净值", endpoint: "/api/admin/jobs/fund_nav_backfill/run?days=180" },
  { key: "fund_nav_backfill_365", label: "回填 365 天净值", endpoint: "/api/admin/jobs/fund_nav_backfill/run?days=365" }
];

const strategyPresets: Array<{
  strategy_type: StrategyType;
  name: string;
  description: string;
  config: Record<string, unknown>;
}> = [
  {
    strategy_type: "momentum_rotation",
    name: "ETF 动量轮动",
    description: "从当前研究池里，每月选出近期表现较强、估值不过热的 Top 3，按等权方式模拟持有。",
    config: {
      platform_profile: "alipay",
      lookback_days: 60,
      rebalance_frequency: "monthly",
      top_n: 3,
      max_pe_percentile: 80,
      initial_cash: 100000,
      fee_rate: 0.001
    }
  },
  {
    strategy_type: "dca_baseline",
    name: "每月定投对照组",
    description: "每月固定投入，作为主动轮动策略的对照曲线。",
    config: {
      target_asset_codes: ["007339"],
      platform_profile: "alipay",
      monthly_amount: 1000,
      day_of_month: 1,
      fee_rate: 0.001
    }
  },
  {
    strategy_type: "screening",
    name: "基金筛选评分",
    description: "复用原推荐页的评分逻辑，只做研究候选排序，不生成买卖指令。",
    config: {}
  }
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

function numberMetric(metrics: Record<string, unknown>, key: string): number | null {
  const value = metrics[key];
  return typeof value === "number" ? value : null;
}

function metricText(metrics: Record<string, unknown>, key: string, mode: "currency" | "percent" | "number") {
  const value = numberMetric(metrics, key);
  if (value === null) {
    return "暂无";
  }
  if (mode === "currency") {
    return formatCurrency(value);
  }
  if (mode === "percent") {
    return formatPercent(value * 100);
  }
  return Number.isInteger(value) ? value.toString() : value.toFixed(2);
}

function strategyLabel(type: StrategyType) {
  if (type === "momentum_rotation") {
    return "动量轮动";
  }
  if (type === "dca_baseline") {
    return "定投";
  }
  return "筛选评分";
}

function orderSideLabel(side: string) {
  return side === "sell" ? "卖出" : "买入";
}

const HOLDING_COLORS = ["#1f5c4b", "#b5532d", "#e3b873", "#6f8f72", "#2f6f9f", "#8b5e34", "#9a7b54", "#5b6c7a"];

function configArray(config: Record<string, unknown>, key: string) {
  const value = config[key];
  return Array.isArray(value) ? value.map(String) : [];
}

function configNumber(config: Record<string, unknown>, key: string, fallback: number) {
  const value = config[key];
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function strategyPoolSize(strategy: StrategyDefinition | null, fallback: number) {
  if (!strategy) {
    return fallback;
  }
  const configuredPool = configArray(strategy.config, "asset_codes");
  const dcaTargets = configArray(strategy.config, "target_asset_codes");
  return configuredPool.length || dcaTargets.length || fallback;
}

function topN(strategy: StrategyDefinition | null) {
  if (!strategy || strategy.strategy_type !== "momentum_rotation") {
    return 0;
  }
  return configNumber(strategy.config, "top_n", 3);
}

function rebalanceText(strategy: StrategyDefinition | null) {
  if (!strategy) {
    return "暂无";
  }
  if (strategy.strategy_type === "dca_baseline") {
    return "每月定投";
  }
  return strategy.config.rebalance_frequency === "monthly" ? "每月调仓" : "按策略规则调仓";
}

function assetLabel(assetCode: string, assetName?: string | null) {
  return assetName ? `${assetName}（${assetCode}）` : assetCode;
}

function latestPositions(run: StrategyRun | null | undefined) {
  if (!run?.positions.length) {
    return [];
  }
  const latestDate = run.positions.reduce((latest, position) => {
    return position.snapshot_date > latest ? position.snapshot_date : latest;
  }, run.positions[0].snapshot_date);
  return run.positions.filter((position) => position.snapshot_date === latestDate && position.market_value > 0);
}

function recentOrders(run: StrategyRun | null | undefined) {
  return [...(run?.orders ?? [])]
    .sort((left, right) => right.trade_date.localeCompare(left.trade_date) || right.id - left.id)
    .slice(0, 10);
}

function equityChartPoints(run: StrategyRun | null | undefined) {
  return (
    run?.equity_curve.map((point) => ({
      date: point.curve_date,
      label: shortDateLabel(point.curve_date),
      equity: point.equity,
      cash: point.cash,
      drawdown: point.drawdown * 100
    })) ?? []
  );
}

function shortDateLabel(value: string) {
  return value.slice(5).replace("-", "/");
}

function reviewNoteText(item: RecommendationReviewItem | undefined, role: string) {
  const value = item?.agent_notes[role];
  return typeof value === "string" ? value : "暂无";
}

function verdictTone(verdict: string | undefined) {
  if (verdict === "可观察") {
    return "bg-emerald-100 text-emerald-800";
  }
  if (verdict === "样本不足") {
    return "bg-amber-100 text-amber-900";
  }
  if (verdict === "不建议采用") {
    return "bg-rose-100 text-rose-800";
  }
  return "bg-blush text-ink";
}

function reviewItemMap(items: RecommendationReviewItem[] | undefined) {
  return new Map((items ?? []).map((item) => [item.asset_code, item]));
}

function shortEtfReviewNoteText(item: ShortEtfReviewItem | undefined, role: string) {
  const value = item?.agent_notes[role];
  return typeof value === "string" ? value : "还没有生成审查。点击上方“生成风控审查”后，这里会补充具体原因。";
}

function shortEtfReviewMap(items: ShortEtfReviewItem[] | undefined) {
  return new Map((items ?? []).map((item) => [item.etf_code, item]));
}

function shortEtfMetricRecord(item: ShortEtfSignalItem) {
  const metrics = item.score_breakdown.metrics;
  return typeof metrics === "object" && metrics !== null && !Array.isArray(metrics)
    ? (metrics as Record<string, unknown>)
    : {};
}

function shortEtfPercentMetric(metrics: Record<string, unknown>, key: string, abs = false) {
  const value = metrics[key];
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "暂无";
  }
  return formatPercent((abs ? Math.abs(value) : value) * 100);
}

function shortEtfTurnoverMetric(metrics: Record<string, unknown>, key: string) {
  const value = metrics[key];
  return typeof value === "number" && Number.isFinite(value) ? formatCurrency(value) : "暂无";
}

function shortEtfMetricCards(item: ShortEtfSignalItem) {
  const metrics = shortEtfMetricRecord(item);
  return [
    { label: "近5日", value: shortEtfPercentMetric(metrics, "return_5d") },
    { label: "近20日", value: shortEtfPercentMetric(metrics, "return_20d") },
    { label: "近60日", value: shortEtfPercentMetric(metrics, "return_60d") },
    { label: "20日波动", value: shortEtfPercentMetric(metrics, "volatility_20d", true) },
    { label: "60日最大回撤", value: shortEtfPercentMetric(metrics, "max_drawdown_60d", true) },
    { label: "20日成交额", value: shortEtfTurnoverMetric(metrics, "average_turnover_20d") }
  ];
}

function shortEtfConclusionMeaning(item: ShortEtfSignalItem) {
  const value = item.rationale.conclusion_meaning;
  if (typeof value === "string") {
    return value;
  }
  if (item.conclusion === "高位观察") {
    return "高位观察 = 分数不低，但风险标签已经触发，先观察，不追高。";
  }
  if (item.conclusion === "可观察") {
    return "可观察 = 暂无主要风险标签，可以继续跟踪，但不是买入建议。";
  }
  return "这个标签只代表研究观察结论，不会自动买卖。";
}

function shortEtfEquityChartPoints(paper: ShortEtfPaper | null | undefined) {
  return (
    paper?.equity_curve.map((point) => ({
      date: point.curve_date,
      label: shortDateLabel(point.curve_date),
      equity: point.equity,
      cash: point.cash,
      drawdown: point.drawdown * 100
    })) ?? []
  );
}

function riskTone(flags: string[]) {
  if (flags.includes("追高风险")) {
    return "bg-amber-100 text-amber-900";
  }
  if (flags.includes("流动性不足") || flags.includes("高波动")) {
    return "bg-rose-100 text-rose-800";
  }
  return "bg-emerald-100 text-emerald-800";
}

function metricNumber(record: Record<string, unknown> | null | undefined, key: string, fallback = 0) {
  const value = record?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function metricString(record: Record<string, unknown> | null | undefined, key: string) {
  const value = record?.[key];
  return typeof value === "string" ? value : null;
}

function metricBoolean(record: Record<string, unknown> | null | undefined, key: string) {
  const value = record?.[key];
  return typeof value === "boolean" ? value : false;
}

function metricArray(record: Record<string, unknown> | null | undefined, key: string) {
  const value = record?.[key];
  return Array.isArray(value) ? value.map(String) : [];
}

function providerLabel(provider: string | null | undefined) {
  if (provider === "akshare") {
    return "AKShare 主源";
  }
  if (provider === "efinance") {
    return "efinance 备用源";
  }
  if (provider === "sina") {
    return "新浪财经备用源";
  }
  return "暂无来源";
}

function healthStatusLabel(status: string, stale: boolean) {
  if (stale) {
    return "数据过期";
  }
  if (status === "success") {
    return "正常";
  }
  if (status === "failed") {
    return "失败";
  }
  return "未同步";
}

function shortEtfHealthProblemItems(status: EtfDataStatus | undefined) {
  return (status?.items ?? [])
    .filter((item) => item.status === "failed" || item.is_stale || item.provider === "efinance")
    .sort((left, right) => {
      const leftScore = left.status === "failed" ? 0 : left.is_stale ? 1 : 2;
      const rightScore = right.status === "failed" ? 0 : right.is_stale ? 1 : 2;
      return leftScore - rightScore || left.code.localeCompare(right.code);
    })
    .slice(0, 8);
}

function shortEtfEvaluationParameterItems(evaluation: ShortEtfEvaluation | null | undefined) {
  return (evaluation?.items ?? [])
    .filter((item) => item.item_type === "parameter")
    .sort((left, right) => right.score - left.score);
}

function shortEtfEvaluationChart(items: ShortEtfEvaluationItem[]) {
  return items.slice(0, 12).map((item) => ({
    label: item.label,
    returnValue: metricNumber(item.metrics, "cumulative_return") * 100,
    drawdown: Math.abs(metricNumber(item.metrics, "max_drawdown")) * 100,
    feeImpact: metricNumber(item.metrics, "fee_impact") * 100
  }));
}

function shortEtfBaselineChart(evaluation: ShortEtfEvaluation | null | undefined) {
  const best = shortEtfEvaluationParameterItems(evaluation)[0];
  const baseline = best?.baseline_metrics ?? metricRecord(evaluation?.summary, "baseline");
  return [
    {
      label: "最佳参数",
      returnValue: best ? metricNumber(best.metrics, "cumulative_return") * 100 : 0,
      drawdown: best ? Math.abs(metricNumber(best.metrics, "max_drawdown")) * 100 : 0
    },
    {
      label: "等权持有",
      returnValue: metricNumber(baseline, "cumulative_return") * 100,
      drawdown: Math.abs(metricNumber(baseline, "max_drawdown")) * 100
    }
  ];
}

function metricRecord(record: Record<string, unknown> | null | undefined, key: string) {
  const value = record?.[key];
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function evaluationItemsByType(evaluation: StrategyEvaluation | null | undefined, itemType: string) {
  return (evaluation?.items ?? []).filter((item) => item.item_type === itemType);
}

function sortedParameterItems(evaluation: StrategyEvaluation | null | undefined) {
  return evaluationItemsByType(evaluation, "parameter_grid").sort((left, right) => right.score - left.score);
}

function baselineItems(evaluation: StrategyEvaluation | null | undefined) {
  return (evaluation?.items ?? []).filter((item) => item.item_type !== "parameter_grid");
}

function defaultEvaluationItem(evaluation: StrategyEvaluation | null | undefined) {
  return (evaluation?.items ?? []).find((item) => item.item_type === "baseline_default") ?? null;
}

function evaluationReturnChart(items: StrategyEvaluationItem[]) {
  return items.map((item) => ({
    label: item.label,
    shortLabel: item.label.replace("动量", "").replace(" 估值", "\n估值"),
    returnValue: metricNumber(item.metrics, "total_return") * 100,
    drawdown: metricNumber(item.metrics, "max_drawdown") * 100,
    score: item.score
  }));
}

function sampleComparisonChart(item: StrategyEvaluationItem | null) {
  if (!item) {
    return [];
  }
  return [
    {
      label: "样本内",
      returnValue: metricNumber(item.in_sample_metrics, "total_return") * 100,
      drawdown: metricNumber(item.in_sample_metrics, "max_drawdown") * 100
    },
    {
      label: "样本外",
      returnValue: metricNumber(item.out_of_sample_metrics, "total_return") * 100,
      drawdown: metricNumber(item.out_of_sample_metrics, "max_drawdown") * 100
    }
  ];
}

function rollingWindowChart(item: StrategyEvaluationItem | null) {
  return (
    item?.rolling_windows.map((window, index) => ({
      label: `${index + 1}`,
      start: metricString(window, "start_date"),
      end: metricString(window, "end_date"),
      returnValue: metricNumber(window, "total_return") * 100,
      drawdown: metricNumber(window, "max_drawdown") * 100
    })) ?? []
  );
}

function StrategyLabClient() {
  const searchParams = useSearchParams();
  const initialView = (searchParams.get("view") as ViewKey | null) ?? "strategies";
  const [view, setView] = useState<ViewKey>(views.some((item) => item.key === initialView) ? initialView : "strategies");
  const [selectedStrategyId, setSelectedStrategyId] = useState<number | null>(null);
  const [selectedPaperId, setSelectedPaperId] = useState<number | null>(null);
  const [selectedEvaluationId, setSelectedEvaluationId] = useState<number | null>(null);
  const [lastRun, setLastRun] = useState<StrategyRun | null>(null);
  const [paper, setPaper] = useState<PaperPortfolio | null>(null);
  const [evaluation, setEvaluation] = useState<StrategyEvaluation | null>(null);
  const [startDate, setStartDate] = useState("2026-01-01");
  const [endDate, setEndDate] = useState("2026-03-31");
  const [evaluationStartDate, setEvaluationStartDate] = useState("2025-06-03");
  const [evaluationEndDate, setEvaluationEndDate] = useState("2026-06-04");
  const [paperStartDate, setPaperStartDate] = useState("2026-02-01");
  const [paperRunDate, setPaperRunDate] = useState("2026-03-31");
  const [shortEtfFromDate, setShortEtfFromDate] = useState("2026-01-01");
  const [shortEtfToDate, setShortEtfToDate] = useState("2026-06-05");
  const [shortEtfPaperStartDate, setShortEtfPaperStartDate] = useState("2026-06-05");
  const [shortEtfPaperRunDate, setShortEtfPaperRunDate] = useState("2026-06-05");
  const [shortEtfPaper, setShortEtfPaper] = useState<ShortEtfPaper | null>(null);
  const [shortEtfResult, setShortEtfResult] = useState<string | null>(null);
  const [dataResult, setDataResult] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const strategies = useQuery({
    queryKey: ["strategy-lab", "strategies"],
    queryFn: async () => (await api.get<StrategyDefinition[]>("/api/strategy-lab/strategies")).data
  });

  const dataStatus = useQuery({
    queryKey: ["admin", "data-status"],
    queryFn: async () => (await api.get<DataStatus>("/api/admin/data-status")).data
  });

  const screening = useQuery({
    queryKey: ["strategy-lab", "screening"],
    queryFn: async () =>
      (await api.get<LatestRecommendationsWithReviewResponse>("/api/recommendations/latest-with-review?asset_type=fund")).data
  });

  const paperPortfolios = useQuery({
    queryKey: ["strategy-lab", "paper"],
    queryFn: async () => (await api.get<PaperPortfolio[]>("/api/strategy-lab/paper")).data
  });

  const evaluations = useQuery({
    queryKey: ["strategy-lab", "evaluations"],
    queryFn: async () => (await api.get<StrategyEvaluation[]>("/api/strategy-lab/evaluations")).data
  });

  const evaluationDetail = useQuery({
    queryKey: ["strategy-lab", "evaluations", selectedEvaluationId],
    enabled: selectedEvaluationId !== null,
    queryFn: async () => (await api.get<StrategyEvaluation>(`/api/strategy-lab/evaluations/${selectedEvaluationId}`)).data
  });

  const shortEtfUniverse = useQuery({
    queryKey: ["short-etf", "universe"],
    enabled: view === "short-etf",
    queryFn: async () => (await api.get<EtfUniverseResponse>("/api/short-etf/universe")).data
  });

  const shortEtfDataStatus = useQuery({
    queryKey: ["short-etf", "data-status"],
    enabled: view === "short-etf",
    queryFn: async () => (await api.get<EtfDataStatus>("/api/short-etf/data-status")).data
  });

  const shortEtfEvaluations = useQuery({
    queryKey: ["short-etf", "evaluations"],
    enabled: view === "short-etf",
    queryFn: async () => (await api.get<ShortEtfEvaluation[]>("/api/short-etf/evaluations")).data
  });

  const latestShortEtfEvaluationId = shortEtfEvaluations.data?.[0]?.id ?? null;
  const shortEtfEvaluationDetail = useQuery({
    queryKey: ["short-etf", "evaluations", latestShortEtfEvaluationId],
    enabled: view === "short-etf" && latestShortEtfEvaluationId !== null,
    queryFn: async () => (await api.get<ShortEtfEvaluation>(`/api/short-etf/evaluations/${latestShortEtfEvaluationId}`)).data
  });

  const shortEtfSignals = useQuery({
    queryKey: ["short-etf", "signals", "latest"],
    enabled: view === "short-etf",
    queryFn: async () => (await api.get<ShortEtfSignalRun | null>("/api/short-etf/signals/latest")).data
  });

  const shortEtfPapers = useQuery({
    queryKey: ["short-etf", "paper"],
    enabled: view === "short-etf",
    queryFn: async () => (await api.get<ShortEtfPaper[]>("/api/short-etf/paper")).data
  });

  const shortEtfReview = useQuery({
    queryKey: ["short-etf", "review", shortEtfSignals.data?.id],
    enabled: view === "short-etf" && Boolean(shortEtfSignals.data?.id),
    retry: false,
    queryFn: async () => {
      try {
        return (await api.get<ShortEtfReview>(`/api/short-etf/signals/${shortEtfSignals.data?.id}/review`)).data;
      } catch {
        return null;
      }
    }
  });

  const selectedStrategy = useMemo(() => {
    const items = strategies.data ?? [];
    return items.find((strategy) => strategy.id === selectedStrategyId) ?? items[0] ?? null;
  }, [selectedStrategyId, strategies.data]);

  useEffect(() => {
    if (selectedPaperId !== null || !paperPortfolios.data?.length) {
      return;
    }
    setSelectedPaperId(paperPortfolios.data[0].id);
  }, [paperPortfolios.data, selectedPaperId]);

  useEffect(() => {
    if (selectedEvaluationId !== null || !evaluations.data?.length) {
      return;
    }
    setSelectedEvaluationId(evaluations.data[0].id);
  }, [evaluations.data, selectedEvaluationId]);

  useEffect(() => {
    if (dataStatus.data?.earliest_nav_date) {
      setEvaluationStartDate(dataStatus.data.earliest_nav_date);
    }
    if (dataStatus.data?.latest_nav_date) {
      setEvaluationEndDate(dataStatus.data.latest_nav_date);
    }
  }, [dataStatus.data?.earliest_nav_date, dataStatus.data?.latest_nav_date]);

  useEffect(() => {
    if (shortEtfPaper || !shortEtfPapers.data?.length) {
      return;
    }
    setShortEtfPaper(shortEtfPapers.data[0]);
  }, [shortEtfPaper, shortEtfPapers.data]);

  const currentPaper = useMemo(() => {
    if (paper && (selectedPaperId === null || paper.id === selectedPaperId)) {
      return paper;
    }
    const items = paperPortfolios.data ?? [];
    return items.find((item) => item.id === selectedPaperId) ?? items[0] ?? null;
  }, [paper, paperPortfolios.data, selectedPaperId]);

  const currentPaperStrategy = useMemo(() => {
    if (!currentPaper) {
      return selectedStrategy;
    }
    return (strategies.data ?? []).find((strategy) => strategy.id === currentPaper.strategy_id) ?? selectedStrategy;
  }, [currentPaper, selectedStrategy, strategies.data]);

  const reconciliation = useQuery({
    queryKey: ["strategy-lab", "paper-reconciliation", currentPaper?.id],
    enabled: Boolean(currentPaper?.id && currentPaper?.latest_run),
    queryFn: async () => (await api.get<PaperReconciliation>(`/api/strategy-lab/paper/${currentPaper?.id}/reconciliation`)).data
  });

  const prepareData = useMutation({
    mutationFn: async (action: DataAction) => (await api.post<Record<string, unknown>>(action.endpoint)).data,
    onSuccess: async (result, action) => {
      setDataResult(`${action.label}完成：${JSON.stringify(result)}`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["admin", "data-status"] }),
        queryClient.invalidateQueries({ queryKey: ["job-runs"] })
      ]);
    }
  });

  const createStrategy = useMutation({
    mutationFn: async (preset: (typeof strategyPresets)[number]) =>
      (
        await api.post<StrategyDefinition>("/api/strategy-lab/strategies", {
          name: preset.name,
          strategy_type: preset.strategy_type,
          asset_type: "fund",
          config: preset.config
        })
      ).data,
    onSuccess: async (strategy) => {
      setSelectedStrategyId(strategy.id);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["strategy-lab", "strategies"] }),
        queryClient.invalidateQueries({ queryKey: ["admin", "data-status"] })
      ]);
    }
  });

  const runBacktest = useMutation({
    mutationFn: async () => {
      if (!selectedStrategy) {
        throw new Error("请先创建或选择一个策略。");
      }
      return (
        await api.post<StrategyRun>(`/api/strategy-lab/strategies/${selectedStrategy.id}/backtests`, {
          start_date: startDate,
          end_date: endDate
        })
      ).data;
    },
    onSuccess: async (run) => {
      setLastRun(run);
      setView(run.run_type === "screening" ? "screening" : "backtests");
      await queryClient.invalidateQueries({ queryKey: ["strategy-lab", "screening"] });
    }
  });

  const runEvaluation = useMutation({
    mutationFn: async () => {
      if (!selectedStrategy) {
        throw new Error("请先创建或选择一个策略。");
      }
      return (
        await api.post<StrategyEvaluation>("/api/strategy-lab/evaluations", {
          strategy_id: selectedStrategy.id,
          start_date: evaluationStartDate,
          end_date: evaluationEndDate
        })
      ).data;
    },
    onSuccess: async (result) => {
      setEvaluation(result);
      setSelectedEvaluationId(result.id);
      setView("evaluation");
      await queryClient.invalidateQueries({ queryKey: ["strategy-lab", "evaluations"] });
    }
  });

  const runRecommendationReview = useMutation({
    mutationFn: async () => {
      if (!screening.data?.run) {
        throw new Error("还没有筛选评分结果，请先运行筛选评分。");
      }
      return (await api.post<RecommendationReview>(`/api/recommendations/runs/${screening.data.run.id}/review`)).data;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["strategy-lab", "screening"] });
    }
  });

  const syncShortEtfData = useMutation({
    mutationFn: async () =>
      (
        await api.post<Record<string, unknown>>("/api/short-etf/data/sync", {
          from_date: shortEtfFromDate,
          to_date: shortEtfToDate
        })
      ).data,
    onSuccess: async (result) => {
      setShortEtfResult(`ETF 数据同步完成：${JSON.stringify(result)}`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["short-etf", "universe"] }),
        queryClient.invalidateQueries({ queryKey: ["short-etf", "data-status"] }),
        queryClient.invalidateQueries({ queryKey: ["job-runs"] })
      ]);
    }
  });

  const retryShortEtfData = useMutation({
    mutationFn: async () => (await api.post<Record<string, unknown>>("/api/short-etf/data/retry-failed")).data,
    onSuccess: async (result) => {
      setShortEtfResult(`失败/过期 ETF 已重试：${JSON.stringify(result)}`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["short-etf", "universe"] }),
        queryClient.invalidateQueries({ queryKey: ["short-etf", "data-status"] }),
        queryClient.invalidateQueries({ queryKey: ["job-runs"] })
      ]);
    }
  });

  const runShortEtfEvaluation = useMutation({
    mutationFn: async () =>
      (
        await api.post<ShortEtfEvaluation>("/api/short-etf/evaluations", {
          start_date: shortEtfFromDate,
          end_date: shortEtfToDate,
          fee_rate: 0.0005
        })
      ).data,
    onSuccess: async (result) => {
      setShortEtfResult(`短线 ETF 可靠性评估完成：${result.conclusion}`);
      await queryClient.invalidateQueries({ queryKey: ["short-etf", "evaluations"] });
    }
  });

  const runShortEtfSignals = useMutation({
    mutationFn: async () => (await api.post<ShortEtfSignalRun>("/api/short-etf/signals/run", {})).data,
    onSuccess: async (run) => {
      setShortEtfResult(`已生成 ${run.items.length} 个 ETF 短线观察信号。`);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["short-etf", "signals", "latest"] }),
        queryClient.invalidateQueries({ queryKey: ["short-etf", "review"] })
      ]);
    }
  });

  const runShortEtfReview = useMutation({
    mutationFn: async () => {
      if (!shortEtfSignals.data) {
        throw new Error("还没有短线 ETF 信号，请先生成信号。");
      }
      return (await api.post<ShortEtfReview>(`/api/short-etf/signals/${shortEtfSignals.data.id}/review`)).data;
    },
    onSuccess: async () => {
      setShortEtfResult("短线 ETF 审查报告已生成。");
      await queryClient.invalidateQueries({ queryKey: ["short-etf", "review", shortEtfSignals.data?.id] });
    }
  });

  const startShortEtfPaper = useMutation({
    mutationFn: async () =>
      (
        await api.post<ShortEtfPaper>("/api/short-etf/paper/start", {
          name: "短线 ETF 模拟盘",
          started_at: shortEtfPaperStartDate,
          initial_cash: 100000
        })
      ).data,
    onSuccess: (paperResult) => {
      setShortEtfPaper(paperResult);
      setShortEtfResult("短线 ETF 模拟盘已启动。");
      queryClient.invalidateQueries({ queryKey: ["short-etf", "paper"] });
    }
  });

  const runShortEtfPaper = useMutation({
    mutationFn: async () => {
      if (!shortEtfPaper) {
        throw new Error("请先启动短线 ETF 模拟盘。");
      }
      return (await api.post<ShortEtfPaper>(`/api/short-etf/paper/${shortEtfPaper.id}/run`, { as_of_date: shortEtfPaperRunDate }))
        .data;
    },
    onSuccess: (paperResult) => {
      setShortEtfPaper(paperResult);
      setShortEtfResult("短线 ETF 模拟盘已更新。");
      queryClient.invalidateQueries({ queryKey: ["short-etf", "paper"] });
    }
  });

  const startPaper = useMutation({
    mutationFn: async () => {
      if (!selectedStrategy) {
        throw new Error("请先创建或选择一个策略。");
      }
      return (
        await api.post<PaperPortfolio>(`/api/strategy-lab/strategies/${selectedStrategy.id}/paper/start`, {
          name: `${selectedStrategy.name} 模拟盘`,
          started_at: paperStartDate
        })
      ).data;
    },
    onSuccess: async (portfolio) => {
      setPaper(portfolio);
      setSelectedPaperId(portfolio.id);
      setView("paper");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["admin", "data-status"] }),
        queryClient.invalidateQueries({ queryKey: ["strategy-lab", "paper"] })
      ]);
    }
  });

  const runPaper = useMutation({
    mutationFn: async () => {
      if (!currentPaper) {
        throw new Error("请先启动一个模拟盘。");
      }
      return (await api.post<PaperPortfolio>(`/api/strategy-lab/paper/${currentPaper.id}/run`, { as_of_date: paperRunDate }))
        .data;
    },
    onSuccess: async (portfolio) => {
      setPaper(portfolio);
      setSelectedPaperId(portfolio.id);
      setLastRun(portfolio.latest_run);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["strategy-lab", "paper-reconciliation", portfolio.id] }),
        queryClient.invalidateQueries({ queryKey: ["strategy-lab", "paper"] }),
        queryClient.invalidateQueries({ queryKey: ["admin", "data-status"] })
      ]);
    }
  });

  const paperRun = currentPaper?.latest_run ?? null;
  const currentEvaluation = evaluation?.id === selectedEvaluationId ? evaluation : evaluationDetail.data ?? null;
  const bestParameterItems = sortedParameterItems(currentEvaluation).slice(0, 12);
  const defaultEvaluation = defaultEvaluationItem(currentEvaluation);
  const evaluationCoverage = currentEvaluation?.data_coverage ?? null;
  const evaluationSummary = currentEvaluation?.summary ?? null;
  const evaluationAssumptions = metricRecord(evaluationSummary, "execution_assumptions");
  const evaluationTurnoverSummary = metricRecord(evaluationSummary, "turnover_summary");
  const evaluationOutOfSampleComparison = metricRecord(evaluationSummary, "out_of_sample_comparison");
  const isSampleSufficient = metricBoolean(evaluationCoverage, "is_sample_sufficient");
  const evaluationParameterChart = evaluationReturnChart(bestParameterItems);
  const evaluationBaselineChart = evaluationReturnChart(baselineItems(currentEvaluation));
  const evaluationSampleChart = sampleComparisonChart(defaultEvaluation);
  const evaluationRollingChart = rollingWindowChart(defaultEvaluation);
  const screeningReviewMap = reviewItemMap(screening.data?.review?.items);
  const activeShortEtfReview = runShortEtfReview.data ?? shortEtfReview.data ?? null;
  const shortEtfReviewItems = shortEtfReviewMap(activeShortEtfReview?.items);
  const shortEtfChartData = shortEtfEquityChartPoints(shortEtfPaper);
  const shortEtfHealthSummary = shortEtfDataStatus.data?.summary ?? null;
  const shortEtfHealthProblems = shortEtfHealthProblemItems(shortEtfDataStatus.data);
  const latestShortEtfEvaluation = runShortEtfEvaluation.data ?? shortEtfEvaluationDetail.data ?? shortEtfEvaluations.data?.[0] ?? null;
  const latestShortEtfEvaluationItems = shortEtfEvaluationParameterItems(latestShortEtfEvaluation);
  const shortEtfEvaluationChartData = shortEtfEvaluationChart(latestShortEtfEvaluationItems);
  const shortEtfBaselineChartData = shortEtfBaselineChart(latestShortEtfEvaluation);
  const shortEtfUniverseCount = shortEtfUniverse.data?.items.length ?? 0;
  const shortEtfLatestPriceDates = (shortEtfUniverse.data?.items ?? [])
    .map((item) => item.latest_price_date)
    .filter((item): item is string => Boolean(item));
  const shortEtfLatestDate = shortEtfLatestPriceDates.sort().at(-1) ?? null;
  const backtestChartData = equityChartPoints(lastRun);
  const paperChartData = equityChartPoints(paperRun);
  const currentPositions = latestPositions(paperRun);
  const currentOrders = recentOrders(paperRun);
  const poolSize = strategyPoolSize(currentPaperStrategy, dataStatus.data?.fund_count ?? 0);
  const currentTopN = topN(currentPaperStrategy);
  const navRows = dataStatus.data?.nav_rows ?? 0;
  const navMissing = !dataStatus.isLoading && navRows === 0;

  return (
    <div className="space-y-8">
      <SectionHeader
        eyebrow="策略实验室"
        title="先回测，再模拟，不直接买"
        description="这里把基金筛选、动量轮动和定投对照放在一起。模拟盘只记录虚拟持仓和虚拟成交，不会连接支付宝账户，也不会真实下单。"
      />

      <Panel className="rounded-[24px]">
        <div className="flex flex-col gap-5 xl:flex-row xl:items-start xl:justify-between">
          <div className="grid flex-1 gap-4 md:grid-cols-3 xl:grid-cols-6">
            <StatPill label="基金数量" value={(dataStatus.data?.fund_count ?? 0).toString()} tone="bg-white text-ink" />
            <StatPill label="净值记录" value={navRows.toString()} />
            <StatPill label="最早净值" value={formatDate(dataStatus.data?.earliest_nav_date)} tone="bg-accentSoft text-ink" />
            <StatPill label="最新净值" value={formatDate(dataStatus.data?.latest_nav_date)} tone="bg-accentSoft text-ink" />
            <StatPill label="策略数量" value={(dataStatus.data?.strategy_count ?? 0).toString()} tone="bg-white text-ink" />
            <StatPill label="模拟盘数量" value={(dataStatus.data?.paper_portfolio_count ?? 0).toString()} />
          </div>
          <div className="flex flex-wrap gap-3 xl:max-w-sm">
            {dataActions.map((action) => (
              <button
                key={action.key}
                className="rounded-full border border-ink/10 px-4 py-2 text-sm font-semibold text-ink transition hover:border-accent disabled:opacity-60"
                disabled={prepareData.isPending}
                onClick={() => prepareData.mutate(action)}
              >
                {prepareData.isPending && prepareData.variables?.key === action.key ? "运行中..." : action.label}
              </button>
            ))}
          </div>
        </div>
        {navMissing ? (
          <p className="mt-4 rounded-[18px] bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-800">
            还没有基金净值数据。请先点“回填 180 天净值”或“回填 365 天净值”，再运行回测或模拟盘。
          </p>
        ) : null}
        {prepareData.isError ? (
          <p className="mt-4 rounded-[18px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
            数据准备失败：{errorText(prepareData.error)}
          </p>
        ) : null}
        {dataResult ? <p className="mt-4 text-xs leading-5 text-ink/55">{dataResult}</p> : null}
      </Panel>

      <div className="grid gap-4 md:grid-cols-3 xl:grid-cols-6">
        {views.map((item) => (
          <button
            key={item.key}
            className={`rounded-[18px] border px-4 py-3 text-left text-sm font-semibold transition ${
              view === item.key ? "border-ink bg-ink text-white" : "border-ink/10 bg-white text-ink hover:border-accent"
            }`}
            onClick={() => setView(item.key)}
          >
            {item.label}
          </button>
        ))}
      </div>

      <div className="grid gap-6 xl:grid-cols-[0.9fr_1.5fr]">
        <Panel className="space-y-5 rounded-[24px]">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.28em] text-accent">策略模板</p>
            <h3 className="mt-2 font-display text-3xl">创建一个可重复运行的策略</h3>
          </div>
          <div className="space-y-3">
            {strategyPresets.map((preset) => (
              <div key={preset.strategy_type} className="rounded-[18px] border border-ink/10 p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="font-semibold text-ink">{preset.name}</p>
                    <p className="mt-1 text-xs leading-5 text-ink/55">{preset.description}</p>
                  </div>
                  <button
                    className="rounded-full bg-ink px-3 py-2 text-xs font-semibold text-white transition hover:bg-pine"
                    onClick={() => createStrategy.mutate(preset)}
                  >
                    添加
                  </button>
                </div>
              </div>
            ))}
          </div>

          <div className="rounded-[18px] bg-paper p-4 text-xs leading-5 text-ink/60">
            第一版只支持基金和 ETF；成交按净值或收盘价估算；可配置手续费；支持支付宝式确认日期；不连接券商或支付宝账户。
          </div>
        </Panel>

        <Panel className="rounded-[24px]">
          <div className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.28em] text-accent">策略列表</p>
              <h3 className="mt-2 font-display text-3xl">已配置策略</h3>
            </div>
            {selectedStrategy ? (
              <div className="rounded-[18px] bg-paper px-4 py-3 text-sm">
                当前选择：<span className="font-semibold">{selectedStrategy.name}</span>
              </div>
            ) : null}
          </div>

          <div className="mt-6 grid gap-3 md:grid-cols-2">
            {(strategies.data ?? []).map((strategy) => (
              <button
                key={strategy.id}
                className={`rounded-[18px] border p-4 text-left transition ${
                  selectedStrategy?.id === strategy.id ? "border-accent bg-blush/50" : "border-ink/10 bg-white"
                }`}
                onClick={() => setSelectedStrategyId(strategy.id)}
              >
                <span className="rounded-full bg-ink px-3 py-1 text-xs font-semibold text-white">
                  {strategyLabel(strategy.strategy_type)}
                </span>
                <p className="mt-3 font-semibold">{strategy.name}</p>
                <p className="mt-2 line-clamp-3 text-xs leading-5 text-ink/55">{JSON.stringify(strategy.config)}</p>
              </button>
            ))}
          </div>

          {!strategies.isLoading && (strategies.data ?? []).length === 0 ? (
            <div className="mt-6 rounded-[18px] border border-dashed border-ink/20 p-5 text-sm text-ink/60">
              还没有策略。先从左侧添加一个模板。
            </div>
          ) : null}
        </Panel>
      </div>

      {view === "backtests" ? (
        <Panel className="space-y-6 rounded-[24px]">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <SectionHeader
              eyebrow="回测"
              title="用历史净值测试策略表现"
              description="选择一个策略和日期区间，查看净值曲线、回撤、收益、交易记录和持仓变化。"
            />
            <div className="flex flex-wrap items-end gap-3">
              <label className="text-xs font-semibold uppercase tracking-[0.2em] text-ink/50">
                开始日期
                <input
                  className="mt-2 block rounded-full border border-ink/10 bg-white px-4 py-2 text-sm normal-case tracking-normal text-ink"
                  type="date"
                  value={startDate}
                  onChange={(event) => setStartDate(event.target.value)}
                />
              </label>
              <label className="text-xs font-semibold uppercase tracking-[0.2em] text-ink/50">
                结束日期
                <input
                  className="mt-2 block rounded-full border border-ink/10 bg-white px-4 py-2 text-sm normal-case tracking-normal text-ink"
                  type="date"
                  value={endDate}
                  onChange={(event) => setEndDate(event.target.value)}
                />
              </label>
              <button
                className="rounded-full bg-accent px-5 py-3 text-sm font-semibold text-white transition hover:bg-pine disabled:opacity-60"
                disabled={runBacktest.isPending || navMissing}
                onClick={() => runBacktest.mutate()}
              >
                {runBacktest.isPending ? "运行中..." : "运行回测"}
              </button>
            </div>
          </div>

          <div className="rounded-[18px] bg-paper px-5 py-4 text-sm leading-7 text-ink/65">
            回测就是拿过去已经发生的基金净值，假装当时按这个策略买卖，再看虚拟资金会怎样变化。它不能预测未来，
            但能帮你看懂一条规则过去是否稳定，尤其要看“最大回撤”：也就是从历史最高点往下最多跌过多少。
          </div>

          {runBacktest.isError ? (
            <p className="rounded-[18px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
              回测失败：{errorText(runBacktest.error)}
            </p>
          ) : null}

          {navMissing ? (
            <div className="rounded-[18px] border border-dashed border-amber-300 bg-amber-50 p-5 text-sm text-amber-800">
              净值历史不足。请先在页面顶部回填净值，再运行回测。
            </div>
          ) : lastRun ? (
            <>
              <div className="grid gap-4 md:grid-cols-4">
                <StatPill label="期末权益" value={metricText(lastRun.metrics, "ending_equity", "currency")} />
                <StatPill label="累计收益" value={metricText(lastRun.metrics, "total_return", "percent")} tone="bg-white text-ink" />
                <StatPill label="最大回撤" value={metricText(lastRun.metrics, "max_drawdown", "percent")} tone="bg-blush text-ink" />
                <StatPill label="交易笔数" value={lastRun.orders.length.toString()} tone="bg-accentSoft text-ink" />
              </div>

              <div className="h-80">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={backtestChartData}>
                    <defs>
                      <linearGradient id="strategyEquityFill" x1="0" x2="0" y1="0" y2="1">
                        <stop offset="5%" stopColor="#1f5c4b" stopOpacity={0.35} />
                        <stop offset="95%" stopColor="#1f5c4b" stopOpacity={0.02} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="label" tick={{ fontSize: 12 }} />
                    <YAxis tick={{ fontSize: 12 }} />
                    <Tooltip formatter={(value: number) => formatCurrency(value)} />
                    <Area dataKey="equity" stroke="#1f5c4b" fill="url(#strategyEquityFill)" strokeWidth={2.5} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>

              <div className="overflow-hidden rounded-[18px] border border-ink/10">
                <div className="grid grid-cols-[110px_1fr_100px_120px] bg-paper px-4 py-3 text-xs font-semibold uppercase tracking-[0.2em] text-ink/50">
                  <span>日期</span>
                  <span>标的</span>
                  <span>方向</span>
                  <span>金额</span>
                </div>
                {lastRun.orders.slice(0, 12).map((order) => (
                  <div key={order.id} className="grid grid-cols-[110px_1fr_100px_120px] border-t border-ink/10 px-4 py-3 text-sm">
                    <span>{formatDate(order.confirmed_date ?? order.trade_date)}</span>
                    <span>{assetLabel(order.asset_code, order.asset_name)}</span>
                    <span>{order.platform === "alipay" ? `${orderSideLabel(order.side)} T+1` : orderSideLabel(order.side)}</span>
                    <span>{formatCurrency(order.amount)}</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="rounded-[18px] border border-dashed border-ink/20 p-5 text-sm text-ink/60">
              还没有回测结果。选择策略后运行一次回测。
            </div>
          )}
        </Panel>
      ) : null}

      {view === "evaluation" ? (
        <Panel className="space-y-6 rounded-[24px]">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <SectionHeader
              eyebrow="可靠性评估"
              title="先检查策略稳不稳，再考虑长期观察"
              description="这里不会训练 AI，也不会自动改模拟盘参数。它只用历史净值做策略体检，帮你看样本够不够、参数稳不稳、回撤能不能接受。"
            />
            <div className="flex flex-wrap items-end gap-3">
              <label className="text-xs font-semibold uppercase tracking-[0.2em] text-ink/50">
                开始日期
                <input
                  className="mt-2 block rounded-full border border-ink/10 bg-white px-4 py-2 text-sm normal-case tracking-normal text-ink"
                  type="date"
                  value={evaluationStartDate}
                  onChange={(event) => setEvaluationStartDate(event.target.value)}
                />
              </label>
              <label className="text-xs font-semibold uppercase tracking-[0.2em] text-ink/50">
                结束日期
                <input
                  className="mt-2 block rounded-full border border-ink/10 bg-white px-4 py-2 text-sm normal-case tracking-normal text-ink"
                  type="date"
                  value={evaluationEndDate}
                  onChange={(event) => setEvaluationEndDate(event.target.value)}
                />
              </label>
              <button
                className="rounded-full bg-accent px-5 py-3 text-sm font-semibold text-white transition hover:bg-pine disabled:opacity-60"
                disabled={runEvaluation.isPending || navMissing}
                onClick={() => runEvaluation.mutate()}
              >
                {runEvaluation.isPending ? "评估中..." : "评估默认策略"}
              </button>
            </div>
          </div>

          <div className="rounded-[18px] bg-paper px-5 py-4 text-sm leading-7 text-ink/65">
            可靠性评估不是预测未来收益。最大回撤 = 曾经从最高点最多亏过多少；样本外 = 没参与挑参数的那段历史；
            参数稳定 = 换一组接近参数后结果不能完全崩。历史少于 2 年时，系统最高只会给“样本不足，继续观察”。
          </div>

          {(evaluations.data ?? []).length > 1 ? (
            <div className="flex flex-wrap gap-3">
              {(evaluations.data ?? []).map((item) => (
                <button
                  key={item.id}
                  className={`rounded-full border px-4 py-2 text-sm font-semibold transition ${
                    selectedEvaluationId === item.id ? "border-ink bg-ink text-white" : "border-ink/10 bg-white text-ink"
                  }`}
                  onClick={() => {
                    setEvaluation(null);
                    setSelectedEvaluationId(item.id);
                  }}
                  type="button"
                >
                  #{item.id} {item.conclusion} · {formatDate(item.end_date)}
                </button>
              ))}
            </div>
          ) : null}

          {runEvaluation.isError ? (
            <p className="rounded-[18px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
              评估失败：{errorText(runEvaluation.error)}
            </p>
          ) : null}

          {navMissing ? (
            <div className="rounded-[18px] border border-dashed border-amber-300 bg-amber-50 p-5 text-sm text-amber-800">
              还没有基金净值数据。请先在页面顶部回填净值，再做可靠性评估。
            </div>
          ) : currentEvaluation ? (
            <>
              <div
                className={`rounded-[22px] border px-5 py-4 ${
                  isSampleSufficient ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-amber-200 bg-amber-50 text-amber-900"
                }`}
              >
                <p className="text-xs font-semibold uppercase tracking-[0.24em] opacity-70">评估结论</p>
                <h3 className="mt-2 font-display text-3xl">{currentEvaluation.conclusion}</h3>
                <p className="mt-2 text-sm leading-6">
                  {currentEvaluation.risk_flags.length
                    ? `风险提示：${currentEvaluation.risk_flags.join("、")}`
                    : "当前历史评估没有触发主要风险提示，但仍不代表未来一定赚钱。"}
                </p>
              </div>

              <div className="grid gap-4 md:grid-cols-5">
                <StatPill label="基金数量" value={metricNumber(evaluationCoverage, "fund_count").toString()} tone="bg-white text-ink" />
                <StatPill label="净值记录" value={metricNumber(evaluationCoverage, "nav_rows").toString()} />
                <StatPill label="最早净值" value={formatDate(metricString(evaluationCoverage, "available_start_date"))} tone="bg-accentSoft text-ink" />
                <StatPill label="最新净值" value={formatDate(metricString(evaluationCoverage, "available_end_date"))} tone="bg-accentSoft text-ink" />
                <StatPill label="2 年门槛" value={isSampleSufficient ? "已满足" : "未满足"} tone={isSampleSufficient ? "bg-white text-ink" : "bg-blush text-ink"} />
              </div>

              {defaultEvaluation ? (
                <div className="grid gap-4 md:grid-cols-5">
                  <StatPill label="默认策略收益" value={metricText(defaultEvaluation.metrics, "total_return", "percent")} />
                  <StatPill label="最大回撤" value={metricText(defaultEvaluation.metrics, "max_drawdown", "percent")} tone="bg-blush text-ink" />
                  <StatPill label="波动率" value={metricText(defaultEvaluation.metrics, "annualized_volatility", "percent")} tone="bg-white text-ink" />
                  <StatPill label="手续费" value={metricText(defaultEvaluation.metrics, "total_fees", "currency")} tone="bg-accentSoft text-ink" />
                  <StatPill label="交易次数" value={metricText(defaultEvaluation.metrics, "trade_count", "number")} />
                </div>
              ) : null}

              <div className="grid gap-4 md:grid-cols-5">
                <StatPill label="手续费假设" value={formatPercent(metricNumber(evaluationAssumptions, "fee_rate") * 100)} tone="bg-white text-ink" />
                <StatPill label="滑点假设" value={formatPercent(metricNumber(evaluationAssumptions, "slippage_rate") * 100)} />
                <StatPill label="默认换手" value={formatPercent(metricNumber(evaluationTurnoverSummary, "default_turnover_rate") * 100)} tone="bg-accentSoft text-ink" />
                <StatPill label="参数中位换手" value={formatPercent(metricNumber(evaluationTurnoverSummary, "median_parameter_turnover_rate") * 100)} />
                <StatPill label="样本外差额" value={metricText(evaluationOutOfSampleComparison, "spread_vs_default", "percent")} tone={metricBoolean(evaluationOutOfSampleComparison, "overfit_warning") ? "bg-blush text-ink" : "bg-white text-ink"} />
              </div>

              <div className="grid gap-6 xl:grid-cols-2">
                <div className="rounded-[22px] border border-ink/10 bg-white p-5">
                  <div className="mb-4">
                    <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">对照组</p>
                    <h3 className="mt-1 font-display text-2xl">默认策略、定投、等权持有放一起看</h3>
                  </div>
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={evaluationBaselineChart}>
                        <CartesianGrid stroke="#eee8dc" vertical={false} />
                        <XAxis dataKey="label" tick={{ fontSize: 12 }} />
                        <YAxis tick={{ fontSize: 12 }} tickFormatter={(value: number) => `${value.toFixed(0)}%`} width={56} />
                        <Tooltip formatter={(value: number | string) => (typeof value === "number" ? `${value.toFixed(2)}%` : value)} />
                        <Bar dataKey="returnValue" name="累计收益" fill="#1f5c4b" radius={[8, 8, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="rounded-[22px] border border-ink/10 bg-white p-5">
                  <div className="mb-4">
                    <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">样本外验证</p>
                    <h3 className="mt-1 font-display text-2xl">前半段和后半段是否差很多</h3>
                  </div>
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={evaluationSampleChart}>
                        <CartesianGrid stroke="#eee8dc" vertical={false} />
                        <XAxis dataKey="label" tick={{ fontSize: 12 }} />
                        <YAxis tick={{ fontSize: 12 }} tickFormatter={(value: number) => `${value.toFixed(0)}%`} width={56} />
                        <Tooltip formatter={(value: number | string) => (typeof value === "number" ? `${value.toFixed(2)}%` : value)} />
                        <Bar dataKey="returnValue" name="阶段收益" fill="#e3b873" radius={[8, 8, 0, 0]} />
                        <Bar dataKey="drawdown" name="阶段最大回撤" fill="#b5532d" radius={[8, 8, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="rounded-[22px] border border-ink/10 bg-white p-5">
                  <div className="mb-4">
                    <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">参数稳定性</p>
                    <h3 className="mt-1 font-display text-2xl">表现靠前的参数组合</h3>
                  </div>
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={evaluationParameterChart}>
                        <CartesianGrid stroke="#eee8dc" vertical={false} />
                        <XAxis dataKey="shortLabel" tick={{ fontSize: 11 }} interval={0} />
                        <YAxis tick={{ fontSize: 12 }} tickFormatter={(value: number) => `${value.toFixed(0)}%`} width={56} />
                        <Tooltip formatter={(value: number | string) => (typeof value === "number" ? `${value.toFixed(2)}%` : value)} />
                        <Bar dataKey="returnValue" name="累计收益" fill="#1f5c4b" radius={[8, 8, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>

                <div className="rounded-[22px] border border-ink/10 bg-white p-5">
                  <div className="mb-4">
                    <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">滚动验证</p>
                    <h3 className="mt-1 font-display text-2xl">不同阶段是否都能撑住</h3>
                  </div>
                  <div className="h-72">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={evaluationRollingChart}>
                        <CartesianGrid stroke="#eee8dc" vertical={false} />
                        <XAxis dataKey="label" tick={{ fontSize: 12 }} />
                        <YAxis tick={{ fontSize: 12 }} tickFormatter={(value: number) => `${value.toFixed(0)}%`} width={56} />
                        <Tooltip
                          formatter={(value: number | string) => (typeof value === "number" ? `${value.toFixed(2)}%` : value)}
                          labelFormatter={(_, items) => {
                            const payload = items?.[0]?.payload;
                            return payload?.start && payload?.end ? `${formatDate(payload.start)} 至 ${formatDate(payload.end)}` : "";
                          }}
                        />
                        <Bar dataKey="returnValue" name="窗口收益" fill="#6f8f72" radius={[8, 8, 0, 0]} />
                        <Bar dataKey="drawdown" name="窗口最大回撤" fill="#b5532d" radius={[8, 8, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              </div>

              <div className="overflow-hidden rounded-[18px] border border-ink/10">
                <div className="grid grid-cols-[1.2fr_90px_100px_100px_100px_1fr] bg-paper px-4 py-3 text-xs font-semibold uppercase tracking-[0.16em] text-ink/50">
                  <span>参数组合</span>
                  <span>评分</span>
                  <span>收益</span>
                  <span>最大回撤</span>
                  <span>交易次数</span>
                  <span>风险提示</span>
                </div>
                {sortedParameterItems(currentEvaluation).map((item) => (
                  <div
                    key={item.id}
                    className="grid grid-cols-[1.2fr_90px_100px_100px_100px_1fr] border-t border-ink/10 px-4 py-3 text-sm"
                  >
                    <span className="font-semibold">{item.label}</span>
                    <span>{item.score.toFixed(2)}</span>
                    <span>{metricText(item.metrics, "total_return", "percent")}</span>
                    <span>{metricText(item.metrics, "max_drawdown", "percent")}</span>
                    <span>{metricText(item.metrics, "trade_count", "number")}</span>
                    <span className="text-ink/55">{item.risk_flags.length ? item.risk_flags.join("、") : "暂无"}</span>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <div className="rounded-[18px] border border-dashed border-ink/20 p-5 text-sm leading-6 text-ink/60">
              还没有可靠性评估结果。选择默认策略后点击“评估默认策略”，系统会正式保存一份研究评估报告。
            </div>
          )}
        </Panel>
      ) : null}

      {view === "paper" ? (
        <Panel className="space-y-6 rounded-[24px]">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
            <SectionHeader
              eyebrow="模拟盘"
              title="不用真金白银，先按规则试跑"
              description="模拟盘会持续保存虚拟持仓和权益，并可以和你手动导入的支付宝真实交易做对账。"
            />
            <div className="flex flex-wrap items-end gap-3">
              <label className="text-xs font-semibold uppercase tracking-[0.2em] text-ink/50">
                启动日期
                <input
                  className="mt-2 block rounded-full border border-ink/10 bg-white px-4 py-2 text-sm normal-case tracking-normal text-ink"
                  type="date"
                  value={paperStartDate}
                  onChange={(event) => setPaperStartDate(event.target.value)}
                />
              </label>
              <button
                className="rounded-full bg-ink px-5 py-3 text-sm font-semibold text-white transition hover:bg-pine disabled:opacity-60"
                disabled={startPaper.isPending || navMissing}
                onClick={() => startPaper.mutate()}
              >
                {startPaper.isPending ? "启动中..." : "启动模拟盘"}
              </button>
              <label className="text-xs font-semibold uppercase tracking-[0.2em] text-ink/50">
                更新到
                <input
                  className="mt-2 block rounded-full border border-ink/10 bg-white px-4 py-2 text-sm normal-case tracking-normal text-ink"
                  type="date"
                  value={paperRunDate}
                  onChange={(event) => setPaperRunDate(event.target.value)}
                />
              </label>
              <button
                className="rounded-full bg-accent px-5 py-3 text-sm font-semibold text-white transition hover:bg-pine disabled:opacity-60"
                disabled={runPaper.isPending || navMissing}
                onClick={() => runPaper.mutate()}
              >
                {runPaper.isPending ? "更新中..." : "更新模拟盘"}
              </button>
            </div>
          </div>

          {(paperPortfolios.data ?? []).length > 1 ? (
            <div className="flex flex-wrap gap-3">
              {(paperPortfolios.data ?? []).map((item) => (
                <button
                  key={item.id}
                  className={`rounded-full border px-4 py-2 text-sm font-semibold transition ${
                    currentPaper?.id === item.id ? "border-ink bg-ink text-white" : "border-ink/10 bg-white text-ink"
                  }`}
                  onClick={() => {
                    setPaper(null);
                    setSelectedPaperId(item.id);
                  }}
                  type="button"
                >
                  #{item.id} {item.name} · {formatDate(item.started_at)}
                </button>
              ))}
            </div>
          ) : null}

          <div className="grid gap-4 md:grid-cols-4">
            <StatPill label="研究池" value={`${poolSize} 只候选`} tone="bg-white text-ink" />
            <StatPill
              label="当前持仓"
              value={`${currentPositions.length || currentTopN || 0} 只`}
              tone="bg-accentSoft text-ink"
            />
            <StatPill label="规则" value={rebalanceText(currentPaperStrategy)} tone="bg-blush text-ink" />
            <StatPill label="最新运行" value={formatDate(paperRun?.as_of_date)} />
          </div>
          <div className="rounded-[18px] bg-paper px-5 py-4 text-sm leading-7 text-ink/65">
            这里的“3 只”是当前持仓数量，不是只研究 3 只基金。默认策略会从研究池里挑出 Top 3 等权持有，
            下次调仓时这 3 只可能会换。模拟盘仍然是虚拟资产，不会连接支付宝，也不会自动买入。
          </div>

          {startPaper.isError ? (
            <p className="rounded-[18px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
              启动失败：{errorText(startPaper.error)}
            </p>
          ) : null}
          {runPaper.isError ? (
            <p className="rounded-[18px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
              更新失败：{errorText(runPaper.error)}
            </p>
          ) : null}

          {navMissing ? (
            <div className="rounded-[18px] border border-dashed border-amber-300 bg-amber-50 p-5 text-sm text-amber-800">
              净值历史不足。请先在页面顶部回填净值，再启动或更新模拟盘。
            </div>
          ) : currentPaper ? (
            <>
              <div className="grid gap-4 md:grid-cols-4">
                <StatPill label="模拟盘权益" value={formatCurrency(currentPaper.latest_equity)} />
                <StatPill label="现金" value={formatCurrency(currentPaper.cash)} tone="bg-white text-ink" />
                <StatPill label="状态" value={currentPaper.status === "active" ? "运行中" : currentPaper.status} tone="bg-accentSoft text-ink" />
                <StatPill label="启动日期" value={formatDate(currentPaper.started_at)} tone="bg-blush text-ink" />
              </div>

              {paperRun ? (
                <div className="grid gap-6 xl:grid-cols-[1.3fr_0.9fr]">
                  <div className="rounded-[22px] border border-ink/10 bg-white p-5">
                    <div className="mb-4 flex items-center justify-between gap-3">
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">权益曲线</p>
                        <h3 className="mt-1 font-display text-2xl">虚拟资金变化</h3>
                      </div>
                      <p className="text-xs text-ink/50">从 {formatDate(currentPaper.started_at)} 到 {formatDate(paperRun.as_of_date)}</p>
                    </div>
                    <div className="h-80">
                      <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={paperChartData}>
                          <defs>
                            <linearGradient id="paperEquityFill" x1="0" x2="0" y1="0" y2="1">
                              <stop offset="5%" stopColor="#1f5c4b" stopOpacity={0.35} />
                              <stop offset="95%" stopColor="#1f5c4b" stopOpacity={0.03} />
                            </linearGradient>
                          </defs>
                          <CartesianGrid stroke="#eee8dc" vertical={false} />
                          <XAxis dataKey="label" tick={{ fontSize: 12 }} />
                          <YAxis tick={{ fontSize: 12 }} width={72} />
                          <Tooltip
                            formatter={(value: number | string) => (typeof value === "number" ? formatCurrency(value) : value)}
                            labelFormatter={(_, items) => items?.[0]?.payload?.date ?? ""}
                          />
                          <Area dataKey="equity" name="模拟盘权益" stroke="#1f5c4b" fill="url(#paperEquityFill)" strokeWidth={2.5} />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                  </div>

                  <div className="rounded-[22px] border border-ink/10 bg-white p-5">
                    <div className="mb-4">
                      <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">当前持仓</p>
                      <h3 className="mt-1 font-display text-2xl">占比一眼看清</h3>
                    </div>
                    {currentPositions.length ? (
                      <>
                        <div className="h-64">
                          <ResponsiveContainer width="100%" height="100%">
                            <PieChart>
                              <Pie
                                data={currentPositions}
                                dataKey="market_value"
                                innerRadius="58%"
                                outerRadius="86%"
                                paddingAngle={3}
                              >
                                {currentPositions.map((position, index) => (
                                  <Cell key={position.asset_code} fill={HOLDING_COLORS[index % HOLDING_COLORS.length]} />
                                ))}
                              </Pie>
                              <Tooltip
                                formatter={(value: number | string) => (typeof value === "number" ? formatCurrency(value) : value)}
                              />
                            </PieChart>
                          </ResponsiveContainer>
                        </div>
                        <div className="space-y-2">
                          {currentPositions.map((position, index) => (
                            <div key={position.asset_code} className="flex items-center justify-between gap-3 text-sm">
                              <span className="flex min-w-0 items-center gap-2">
                                <span
                                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                                  style={{ backgroundColor: HOLDING_COLORS[index % HOLDING_COLORS.length] }}
                                />
                                <span className="truncate">{assetLabel(position.asset_code, position.asset_name)}</span>
                              </span>
                              <span className="shrink-0 font-semibold">{formatPercent(position.weight * 100)}</span>
                            </div>
                          ))}
                        </div>
                      </>
                    ) : (
                      <div className="rounded-[18px] border border-dashed border-ink/20 p-5 text-sm text-ink/60">
                        当前模拟盘还没有持仓。先更新一次模拟盘。
                      </div>
                    )}
                  </div>

                  <div className="rounded-[22px] border border-ink/10 bg-white p-5 xl:col-span-2">
                    <div className="mb-4">
                      <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">最大回撤</p>
                      <h3 className="mt-1 font-display text-2xl">看中途最多难受过多少</h3>
                    </div>
                    <div className="h-60">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={paperChartData}>
                          <CartesianGrid stroke="#eee8dc" vertical={false} />
                          <XAxis dataKey="label" tick={{ fontSize: 12 }} />
                          <YAxis tick={{ fontSize: 12 }} tickFormatter={(value: number) => `${value.toFixed(0)}%`} width={56} />
                          <Tooltip
                            formatter={(value: number | string) =>
                              typeof value === "number" ? `${value.toFixed(2)}%` : value
                            }
                            labelFormatter={(_, items) => items?.[0]?.payload?.date ?? ""}
                          />
                          <Bar dataKey="drawdown" name="从高点回落" fill="#b5532d" radius={[8, 8, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="rounded-[18px] border border-dashed border-ink/20 p-5 text-sm text-ink/60">
                  这个模拟盘还没有运行结果。点击“更新模拟盘”后会生成权益曲线、回撤图和持仓占比图。
                </div>
              )}

              {paperRun ? (
                <div className="grid gap-6 xl:grid-cols-2">
                  <div className="overflow-hidden rounded-[18px] border border-ink/10">
                    <div className="bg-paper px-4 py-3">
                      <p className="text-xs font-semibold uppercase tracking-[0.2em] text-ink/50">当前持仓明细</p>
                    </div>
                    <div className="grid grid-cols-[1fr_120px_100px] px-4 py-3 text-xs font-semibold text-ink/50">
                      <span>基金</span>
                      <span>市值</span>
                      <span>占比</span>
                    </div>
                    {currentPositions.map((position) => (
                      <div key={position.asset_code} className="grid grid-cols-[1fr_120px_100px] border-t border-ink/10 px-4 py-3 text-sm">
                        <span className="min-w-0 font-semibold">{assetLabel(position.asset_code, position.asset_name)}</span>
                        <span>{formatCurrency(position.market_value)}</span>
                        <span>{formatPercent(position.weight * 100)}</span>
                      </div>
                    ))}
                  </div>

                  <div className="overflow-hidden rounded-[18px] border border-ink/10">
                    <div className="bg-paper px-4 py-3">
                      <p className="text-xs font-semibold uppercase tracking-[0.2em] text-ink/50">最近虚拟交易</p>
                    </div>
                    <div className="grid grid-cols-[100px_1fr_78px_110px] px-4 py-3 text-xs font-semibold text-ink/50">
                      <span>日期</span>
                      <span>基金</span>
                      <span>方向</span>
                      <span>金额</span>
                    </div>
                    {currentOrders.map((order) => (
                      <div key={order.id} className="grid grid-cols-[100px_1fr_78px_110px] border-t border-ink/10 px-4 py-3 text-sm">
                        <span>{formatDate(order.confirmed_date ?? order.trade_date)}</span>
                        <span className="min-w-0 font-semibold">{assetLabel(order.asset_code, order.asset_name)}</span>
                        <span>{order.platform === "alipay" ? `${orderSideLabel(order.side)} T+1` : orderSideLabel(order.side)}</span>
                        <span>{formatCurrency(order.amount)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}

              {reconciliation.data ? (
                <div className="overflow-hidden rounded-[18px] border border-ink/10">
                  <div className="grid gap-3 bg-paper px-4 py-4 text-sm md:grid-cols-4">
                    <StatPill label="真实账本资产" value={formatCurrency(reconciliation.data.actual_equity)} tone="bg-white text-ink" />
                    <StatPill label="模拟差额" value={formatCurrency(reconciliation.data.equity_diff)} tone="bg-accentSoft text-ink" />
                    <StatPill label="平台规则" value={reconciliation.data.platform_profile === "alipay" ? "支付宝式" : reconciliation.data.platform_profile} tone="bg-blush text-ink" />
                    <StatPill label="数据日期" value={formatDate(reconciliation.data.as_of_date)} tone="bg-white text-ink" />
                  </div>
                  <div className="grid grid-cols-[1fr_110px_110px_120px] px-4 py-3 text-xs font-semibold uppercase tracking-[0.2em] text-ink/50">
                    <span>基金</span>
                    <span>模拟份额</span>
                    <span>真实份额</span>
                    <span>差额</span>
                  </div>
                  {reconciliation.data.items.slice(0, 8).map((item) => (
                    <div key={item.asset_code} className="grid grid-cols-[1fr_110px_110px_120px] border-t border-ink/10 px-4 py-3 text-sm">
                      <span className="font-semibold">{assetLabel(item.asset_code, item.asset_name)}</span>
                      <span>{item.paper_shares.toFixed(2)}</span>
                      <span>{item.actual_shares.toFixed(2)}</span>
                      <span>{item.share_diff.toFixed(2)}</span>
                    </div>
                  ))}
                  <p className="border-t border-ink/10 px-4 py-3 text-xs leading-5 text-ink/55">
                    真实账本只来自你手动录入或导入的交易；系统不会连接支付宝账户。
                  </p>
                </div>
              ) : null}
            </>
          ) : (
            <div className="rounded-[18px] border border-dashed border-ink/20 p-5 text-sm text-ink/60">
              服务器上还没有模拟盘。选择一个策略后点击“启动模拟盘”，或者到后台任务页运行默认策略初始化。
            </div>
          )}
        </Panel>
      ) : null}

      {view === "short-etf" ? (
        <Panel className="space-y-6 rounded-[24px]">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <SectionHeader
              eyebrow="短线 ETF"
              title="看风口，也要先看追高风险"
              description="这里只做场内 ETF 的短线研究和模拟。它不连接券商，不会真实下单；股票类 ETF 按 T+1 保守模拟，不做盘中高频。"
            />
            <div className="flex flex-wrap gap-3">
              <button
                className="rounded-full border border-ink/10 px-4 py-2 text-sm font-semibold text-ink hover:border-accent disabled:opacity-60"
                disabled={syncShortEtfData.isPending}
                onClick={() => syncShortEtfData.mutate()}
              >
                {syncShortEtfData.isPending ? "同步中..." : "同步 ETF 数据"}
              </button>
              <button
                className="rounded-full border border-ink/10 px-4 py-2 text-sm font-semibold text-ink hover:border-accent disabled:opacity-60"
                disabled={retryShortEtfData.isPending}
                onClick={() => retryShortEtfData.mutate()}
              >
                {retryShortEtfData.isPending ? "重试中..." : "重试失败/过期 ETF"}
              </button>
              <button
                className="rounded-full bg-ink px-4 py-2 text-sm font-semibold text-white hover:bg-pine disabled:opacity-60"
                disabled={runShortEtfSignals.isPending}
                onClick={() => runShortEtfSignals.mutate()}
              >
                {runShortEtfSignals.isPending ? "生成中..." : "生成短线信号"}
              </button>
              <button
                className="rounded-full bg-accent px-4 py-2 text-sm font-semibold text-white hover:bg-pine disabled:opacity-60"
                disabled={runShortEtfReview.isPending || !shortEtfSignals.data}
                onClick={() => runShortEtfReview.mutate()}
              >
                {runShortEtfReview.isPending ? "审查中..." : "生成风控审查"}
              </button>
              <button
                className="rounded-full bg-pine px-4 py-2 text-sm font-semibold text-white hover:bg-ink disabled:opacity-60"
                disabled={runShortEtfEvaluation.isPending}
                onClick={() => runShortEtfEvaluation.mutate()}
              >
                {runShortEtfEvaluation.isPending ? "评估中..." : "评估规则可靠性"}
              </button>
            </div>
          </div>

          <div className="grid gap-4 md:grid-cols-4 xl:grid-cols-8">
            <StatPill label="ETF 池" value={`${metricNumber(shortEtfHealthSummary, "etfs", shortEtfUniverseCount)} 只`} tone="bg-white text-ink" />
            <StatPill label="健康数据" value={`${metricNumber(shortEtfHealthSummary, "healthy")} 只`} />
            <StatPill label="同步失败" value={`${metricNumber(shortEtfHealthSummary, "failed")} 只`} tone="bg-blush text-ink" />
            <StatPill label="数据过期" value={`${metricNumber(shortEtfHealthSummary, "stale")} 只`} tone="bg-accentSoft text-ink" />
            <StatPill label="备用源补齐" value={`${metricNumber(shortEtfHealthSummary, "fallback_used")} 只`} tone="bg-white text-ink" />
            <StatPill label="最新行情" value={formatDate(shortEtfLatestDate ?? metricString(shortEtfHealthSummary, "latest_price_date"))} />
            <StatPill label="信号日期" value={formatDate(shortEtfSignals.data?.as_of_date)} tone="bg-accentSoft text-ink" />
            <StatPill label="模拟权益" value={formatCurrency(shortEtfPaper?.latest_equity ?? 0)} tone="bg-blush text-ink" />
          </div>

          <div className="rounded-[18px] bg-paper px-5 py-4 text-sm leading-7 text-ink/65">
            场外基金、一年持有期基金和封闭期基金不进入这里。科技、光伏、电力设备、半导体等主题只是标签；
            如果短期涨太快，系统会优先提示“追高风险”，不会把风口当成买入理由。公开日线数据通常会有延迟，
            这里也不是支付宝实时盘口；所有结果只用于研究观察，不是买入、卖出或目标价建议。模拟盘只会虚拟买入“可观察”的标的；
            如果当天没有“可观察”，就保持现金，不追高。
          </div>

          <div className="grid gap-3 md:grid-cols-[1fr_1fr_1fr_1fr_auto]">
            <label className="text-sm text-ink/60">
              同步开始
              <input
                className="mt-2 w-full rounded-full border border-ink/10 px-4 py-2 text-ink"
                type="date"
                value={shortEtfFromDate}
                onChange={(event) => setShortEtfFromDate(event.target.value)}
              />
            </label>
            <label className="text-sm text-ink/60">
              同步结束
              <input
                className="mt-2 w-full rounded-full border border-ink/10 px-4 py-2 text-ink"
                type="date"
                value={shortEtfToDate}
                onChange={(event) => {
                  setShortEtfToDate(event.target.value);
                  setShortEtfPaperRunDate(event.target.value);
                }}
              />
            </label>
            <label className="text-sm text-ink/60">
              模拟启动
              <input
                className="mt-2 w-full rounded-full border border-ink/10 px-4 py-2 text-ink"
                type="date"
                value={shortEtfPaperStartDate}
                onChange={(event) => setShortEtfPaperStartDate(event.target.value)}
              />
            </label>
            <label className="text-sm text-ink/60">
              模拟更新到
              <input
                className="mt-2 w-full rounded-full border border-ink/10 px-4 py-2 text-ink"
                type="date"
                value={shortEtfPaperRunDate}
                onChange={(event) => setShortEtfPaperRunDate(event.target.value)}
              />
            </label>
            <div className="flex items-end gap-3">
              <button
                className="rounded-full border border-ink/10 px-4 py-2 text-sm font-semibold text-ink hover:border-accent disabled:opacity-60"
                disabled={startShortEtfPaper.isPending}
                onClick={() => startShortEtfPaper.mutate()}
              >
                {startShortEtfPaper.isPending ? "启动中..." : "启动模拟盘"}
              </button>
              <button
                className="rounded-full bg-ink px-4 py-2 text-sm font-semibold text-white hover:bg-pine disabled:opacity-60"
                disabled={runShortEtfPaper.isPending || !shortEtfPaper}
                onClick={() => runShortEtfPaper.mutate()}
              >
                {runShortEtfPaper.isPending ? "更新中..." : "更新模拟盘"}
              </button>
            </div>
          </div>

          {[
            syncShortEtfData,
            retryShortEtfData,
            runShortEtfSignals,
            runShortEtfReview,
            runShortEtfEvaluation,
            startShortEtfPaper,
            runShortEtfPaper
          ].some((mutation) => mutation.isError) ? (
            <p className="rounded-[18px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
              操作失败：
              {errorText(
                syncShortEtfData.error ??
                  retryShortEtfData.error ??
                  runShortEtfSignals.error ??
                  runShortEtfReview.error ??
                  runShortEtfEvaluation.error ??
                  startShortEtfPaper.error ??
                  runShortEtfPaper.error
              )}
            </p>
          ) : null}
          {shortEtfResult ? <p className="text-xs leading-5 text-ink/55">{shortEtfResult}</p> : null}

          <div className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
            <div className="rounded-[20px] border border-ink/10 bg-white p-5">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">数据体检</p>
                  <h3 className="mt-2 text-xl font-semibold text-ink">先确认行情够不够干净</h3>
                </div>
                <span className="rounded-full bg-paper px-3 py-1 text-xs font-semibold text-ink/60">
                  {shortEtfDataStatus.isLoading ? "读取中" : "已读取"}
                </span>
              </div>
              <div className="mt-4 space-y-3 text-sm leading-6 text-ink/65">
                <p>主源是 AKShare；单只 ETF 拉取失败时，会再尝试 efinance 备用源。备用源补齐不是坏事，但说明主源当时不稳定。</p>
                <p>失败或过期的 ETF 不会阻塞整个池子，你可以点“重试失败/过期 ETF”单独补它们。</p>
              </div>
            </div>

            <div className="rounded-[20px] border border-ink/10 bg-white p-5">
              <p className="font-semibold text-ink">需要关注的数据项</p>
              <div className="mt-4 space-y-3">
                {shortEtfHealthProblems.map((item) => (
                  <div key={item.code} className="grid gap-2 rounded-[16px] bg-paper px-4 py-3 text-sm md:grid-cols-[1fr_92px_120px]">
                    <div>
                      <p className="font-semibold text-ink">{item.name || item.code}</p>
                      <p className="text-xs text-ink/50">
                        {item.code} · 最新 {formatDate(item.latest_price_date)} · 成功 {item.successful_rows} 行
                      </p>
                      {item.last_error_message ? <p className="mt-1 text-xs text-rose-700">{item.last_error_message}</p> : null}
                    </div>
                    <span className="self-center rounded-full bg-white px-3 py-1 text-xs font-semibold text-ink/65">
                      {healthStatusLabel(item.status, item.is_stale)}
                    </span>
                    <span className="self-center text-xs text-ink/55">{providerLabel(item.provider)}</span>
                  </div>
                ))}
                {!shortEtfDataStatus.isLoading && shortEtfHealthProblems.length === 0 ? (
                  <div className="rounded-[18px] border border-dashed border-ink/20 p-5 text-sm text-ink/60">
                    暂无失败、过期或备用源补齐记录。后续同步失败时会显示具体 ETF 和原因。
                  </div>
                ) : null}
              </div>
            </div>
          </div>

          <div className="rounded-[20px] border border-ink/10 bg-white p-5">
            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">可靠性评估</p>
                <h3 className="mt-2 text-xl font-semibold text-ink">这套短线规则历史上稳不稳</h3>
                <p className="mt-2 text-sm leading-6 text-ink/60">
                  评估会重放历史日线，比较不同动量窗口和持仓数量，并扣掉手续费。样本太短时，只能标记“样本不足，继续观察”。
                </p>
              </div>
              {latestShortEtfEvaluation ? (
                <span className="rounded-full bg-accentSoft px-4 py-2 text-sm font-semibold text-ink">
                  {latestShortEtfEvaluation.conclusion}
                </span>
              ) : null}
            </div>

            {latestShortEtfEvaluation ? (
              <>
                <div className="mt-5 grid gap-4 md:grid-cols-5">
                  <StatPill label="样本天数" value={`${latestShortEtfEvaluation.sample_days} 天`} tone="bg-white text-ink" />
                  <StatPill label="数据覆盖" value={`${metricNumber(latestShortEtfEvaluation.data_coverage, "priced_etf_count")} / ${metricNumber(latestShortEtfEvaluation.data_coverage, "etf_count")} 只`} />
                  <StatPill label="参数稳定" value={metricString(latestShortEtfEvaluation.summary, "parameter_stability") ?? "暂无"} tone="bg-accentSoft text-ink" />
                  <StatPill label="最好收益" value={formatPercent(metricNumber(latestShortEtfEvaluation.summary, "best_return") * 100)} tone="bg-white text-ink" />
                  <StatPill label="手续费假设" value={formatPercent(metricNumber(latestShortEtfEvaluation.summary, "fee_rate") * 100)} />
                </div>

                {latestShortEtfEvaluation.risk_flags.length ? (
                  <p className="mt-4 rounded-[18px] bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-900">
                    风险提示：{latestShortEtfEvaluation.risk_flags.join("、")}。这会限制结论强度，不会自动改默认信号参数。
                  </p>
                ) : null}

                <div className="mt-6 grid gap-6 xl:grid-cols-2">
                  <div className="rounded-[18px] bg-paper p-4">
                    <p className="font-semibold text-ink">参数表现对比</p>
                    <div className="mt-4 h-72">
                      {shortEtfEvaluationChartData.length ? (
                        <ResponsiveContainer width="100%" height="100%">
                          <BarChart data={shortEtfEvaluationChartData}>
                            <CartesianGrid stroke="#eadfd2" vertical={false} />
                            <XAxis dataKey="label" tick={{ fontSize: 11 }} />
                            <YAxis tickFormatter={(value: number) => `${value.toFixed(0)}%`} width={56} />
                            <Tooltip formatter={(value: number | string) => (typeof value === "number" ? `${value.toFixed(2)}%` : value)} />
                            <Bar dataKey="returnValue" name="累计收益" fill="#1f5c4b" radius={[8, 8, 0, 0]} />
                            <Bar dataKey="drawdown" name="最大回撤" fill="#b5532d" radius={[8, 8, 0, 0]} />
                          </BarChart>
                        </ResponsiveContainer>
                      ) : (
                        <div className="flex h-full items-center justify-center rounded-[18px] bg-white text-sm text-ink/50">
                          还没有参数明细。点击“评估规则可靠性”后生成。
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="rounded-[18px] bg-paper p-4">
                    <p className="font-semibold text-ink">最佳参数 vs 等权持有</p>
                    <div className="mt-4 h-72">
                      <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={shortEtfBaselineChartData}>
                          <CartesianGrid stroke="#eadfd2" vertical={false} />
                          <XAxis dataKey="label" tick={{ fontSize: 12 }} />
                          <YAxis tickFormatter={(value: number) => `${value.toFixed(0)}%`} width={56} />
                          <Tooltip formatter={(value: number | string) => (typeof value === "number" ? `${value.toFixed(2)}%` : value)} />
                          <Bar dataKey="returnValue" name="累计收益" fill="#1f5c4b" radius={[8, 8, 0, 0]} />
                          <Bar dataKey="drawdown" name="最大回撤" fill="#b5532d" radius={[8, 8, 0, 0]} />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </div>
                </div>

                <div className="mt-6 overflow-hidden rounded-[18px] border border-ink/10">
                  <div className="grid grid-cols-[1fr_110px_110px_110px_1.5fr] bg-paper px-4 py-3 text-xs font-semibold text-ink/50">
                    <span>参数</span>
                    <span>累计收益</span>
                    <span>最大回撤</span>
                    <span>手续费影响</span>
                    <span>入选 ETF</span>
                  </div>
                  {latestShortEtfEvaluationItems.slice(0, 12).map((item) => (
                    <div key={item.id} className="grid grid-cols-[1fr_110px_110px_110px_1.5fr] border-t border-ink/10 px-4 py-3 text-sm">
                      <span className="font-semibold">{item.label}</span>
                      <span>{formatPercent(metricNumber(item.metrics, "cumulative_return") * 100)}</span>
                      <span>{formatPercent(Math.abs(metricNumber(item.metrics, "max_drawdown")) * 100)}</span>
                      <span>{formatPercent(metricNumber(item.metrics, "fee_impact") * 100)}</span>
                      <span className="truncate text-ink/60">{metricArray(item.metrics, "selected_codes").join("、") || "暂无"}</span>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <div className="mt-5 rounded-[18px] border border-dashed border-ink/20 p-5 text-sm text-ink/60">
                还没有可靠性评估。先同步一段 ETF 日线数据，再点击“评估规则可靠性”。
              </div>
            )}
          </div>

          <div className="grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
            <div className="space-y-4">
              {(shortEtfSignals.data?.items ?? []).slice(0, 10).map((item) => {
                const review = shortEtfReviewItems.get(item.etf_code);
                return (
                  <div key={item.id} className="rounded-[20px] border border-ink/10 bg-white p-5">
                    <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">#{item.rank} 短线观察</p>
                        <h3 className="mt-2 text-xl font-semibold text-ink">
                          {item.etf_name ?? item.etf_code}
                          <span className="ml-2 text-sm font-normal text-ink/45">{item.etf_code}</span>
                        </h3>
                        <p className="mt-2 text-xs text-ink/50">{item.theme_tags.join(" / ")} · {item.trading_rule_label}</p>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <span className="rounded-full bg-ink px-3 py-1 text-xs font-semibold text-white">
                          {item.total_score.toFixed(1)} 分
                        </span>
                        <span className={`rounded-full px-3 py-1 text-xs font-semibold ${riskTone(item.risk_flags)}`}>
                          {item.conclusion}
                        </span>
                      </div>
                    </div>
                    <div className="mt-4 grid gap-3 md:grid-cols-2">
                      <div className="rounded-[16px] bg-paper p-4 text-sm leading-6 text-ink/65">
                        <p className="font-semibold text-ink">风险标签</p>
                        <p className="mt-1">{item.risk_flags.length ? item.risk_flags.join("、") : "暂无明显风险标签"}</p>
                      </div>
                      <div className="rounded-[16px] bg-paper p-4 text-sm leading-6 text-ink/65">
                        <p className="font-semibold text-ink">规则说明</p>
                        <p className="mt-1">{String(item.rationale.reason ?? "按趋势、流动性和风险扣分生成。")}</p>
                        {item.rationale.opportunity ? <p className="mt-2 text-xs text-ink/55">机会：{String(item.rationale.opportunity)}</p> : null}
                        {item.rationale.danger ? <p className="mt-1 text-xs text-ink/55">危险：{String(item.rationale.danger)}</p> : null}
                      </div>
                      <div className="rounded-[16px] bg-white p-4 text-sm leading-6 text-ink/65 ring-1 ring-ink/10 md:col-span-2">
                        <p className="font-semibold text-ink">为什么是“{item.conclusion}”</p>
                        <p className="mt-1">{shortEtfConclusionMeaning(item)}</p>
                        <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                          {shortEtfMetricCards(item).map((metric) => (
                            <div key={metric.label} className="rounded-[12px] bg-paper px-3 py-2">
                              <p className="text-xs text-ink/45">{metric.label}</p>
                              <p className="mt-1 font-semibold text-ink">{metric.value}</p>
                            </div>
                          ))}
                        </div>
                      </div>
                      {["数据员", "趋势员", "风控员", "反方", "总结员"].map((role) => (
                        <div key={role} className="rounded-[16px] bg-white p-4 text-sm leading-6 text-ink/65 ring-1 ring-ink/10">
                          <p className="font-semibold text-ink">{role}</p>
                          <p className="mt-1">{shortEtfReviewNoteText(review, role)}</p>
                        </div>
                      ))}
                    </div>
                  </div>
                );
              })}
              {!shortEtfSignals.isLoading && (shortEtfSignals.data?.items ?? []).length === 0 ? (
                <div className="rounded-[18px] border border-dashed border-ink/20 p-5 text-sm text-ink/60">
                  还没有短线 ETF 信号。先同步 ETF 数据，再点击“生成短线信号”。
                </div>
              ) : null}
            </div>

            <div className="space-y-6">
              <div className="rounded-[20px] border border-ink/10 bg-white p-5">
                <p className="font-semibold text-ink">模拟盘权益</p>
                <div className="mt-4 h-64">
                  {shortEtfChartData.length ? (
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={shortEtfChartData}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#eadfd2" />
                        <XAxis dataKey="label" tickLine={false} axisLine={false} />
                        <YAxis tickFormatter={(value) => `${Number(value / 10000).toFixed(1)}万`} tickLine={false} axisLine={false} />
                        <Tooltip formatter={(value) => formatCurrency(Number(value))} />
                        <Area type="monotone" dataKey="equity" stroke="#1f5c4b" fill="#dce9df" />
                      </AreaChart>
                    </ResponsiveContainer>
                  ) : (
                    <div className="flex h-full items-center justify-center rounded-[18px] bg-paper text-sm text-ink/50">
                      启动并更新模拟盘后显示权益曲线。
                    </div>
                  )}
                </div>
              </div>

              <div className="rounded-[20px] border border-ink/10 bg-white p-5">
                <p className="font-semibold text-ink">当前持仓</p>
                <div className="mt-4 space-y-3">
                  {(shortEtfPaper?.positions ?? []).map((position) => (
                    <div key={position.etf_code} className="flex items-center justify-between rounded-[16px] bg-paper px-4 py-3 text-sm">
                      <span className="font-semibold">{position.etf_name ?? position.etf_code}</span>
                      <span>{formatCurrency(position.market_value)} · {formatPercent(position.weight * 100)}</span>
                    </div>
                  ))}
                  {!shortEtfPaper?.positions.length ? <p className="text-sm text-ink/55">暂无持仓。</p> : null}
                </div>
              </div>

              <div className="rounded-[20px] border border-ink/10 bg-white p-5">
                <p className="font-semibold text-ink">最近虚拟成交</p>
                <div className="mt-4 space-y-3">
                  {(shortEtfPaper?.orders ?? []).slice(-6).reverse().map((order) => (
                    <div key={order.id} className="grid grid-cols-[1fr_70px_90px] gap-2 rounded-[16px] bg-paper px-4 py-3 text-sm">
                      <span className="font-semibold">{order.etf_name ?? order.etf_code}</span>
                      <span>{order.side === "sell" ? "卖出" : "买入"}</span>
                      <span>{formatDate(order.trade_date)}</span>
                    </div>
                  ))}
                  {!shortEtfPaper?.orders.length ? <p className="text-sm text-ink/55">暂无虚拟成交。</p> : null}
                </div>
              </div>
            </div>
          </div>
        </Panel>
      ) : null}

      {view === "screening" ? (
        <Panel className="space-y-6 rounded-[24px]">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <SectionHeader
              eyebrow="筛选评分"
              title="研究候选，不是购买指令"
              description="先用确定性规则排序，再用多角色审查解释和质疑。审查报告不会改变原始排名，也不会自动买基金。"
            />
            <button
              className="rounded-full bg-accent px-5 py-3 text-sm font-semibold text-white transition hover:bg-pine disabled:opacity-60"
              disabled={runRecommendationReview.isPending || !screening.data?.run}
              onClick={() => runRecommendationReview.mutate()}
            >
              {runRecommendationReview.isPending ? "生成中..." : "生成研究审查报告"}
            </button>
          </div>
          <div className="rounded-[18px] bg-paper px-5 py-4 text-sm leading-7 text-ink/65">
            这个审查借鉴 TradingAgents 的“多角色研究”思想：数据员看数据够不够，策略员解释为什么入选，风控员看风险，反方专门挑问题，
            总结员只给“可观察 / 谨慎 / 样本不足 / 不建议采用”。它不会把 AI 输出当成买入信号。
          </div>
          {runRecommendationReview.isError ? (
            <p className="rounded-[18px] bg-rose-50 px-4 py-3 text-sm text-rose-700">
              审查失败：{errorText(runRecommendationReview.error)}
            </p>
          ) : null}
          {screening.data?.review ? (
            <div className="grid gap-4 md:grid-cols-4">
              <StatPill label="审查模型" value={screening.data.review.model_name} tone="bg-white text-ink" />
              <StatPill label="候选数量" value={String(screening.data.review.summary.item_count ?? 0)} />
              <StatPill
                label="策略验证"
                value={
                  typeof screening.data.review.summary.strategy_evaluation === "object" &&
                  screening.data.review.summary.strategy_evaluation !== null &&
                  "conclusion" in screening.data.review.summary.strategy_evaluation
                    ? String(screening.data.review.summary.strategy_evaluation.conclusion ?? "暂无")
                    : "暂无"
                }
                tone="bg-accentSoft text-ink"
              />
              <StatPill label="生成时间" value={formatDate(screening.data.review.finished_at)} tone="bg-blush text-ink" />
            </div>
          ) : screening.data?.run ? (
            <div className="rounded-[18px] border border-dashed border-ink/20 p-5 text-sm text-ink/60">
              已有筛选评分，但还没有研究审查报告。点击“生成研究审查报告”后，会为每只候选基金补充解释、风险和反方观点。
            </div>
          ) : null}
          {(screening.data?.items ?? []).map((item) => {
            const reviewItem = screeningReviewMap.get(item.asset_code);
            return (
              <div key={item.id} className="rounded-[20px] border border-ink/10 bg-white p-5">
                <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                  <div>
                    <p className="text-xs font-semibold uppercase tracking-[0.22em] text-accent">#{item.rank} 研究候选</p>
                    <h3 className="mt-2 text-xl font-semibold text-ink">
                      {item.asset_name}
                      <span className="ml-2 text-sm font-normal text-ink/45">{item.asset_code}</span>
                    </h3>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <span className="rounded-full bg-ink px-3 py-1 text-xs font-semibold text-white">
                      评分 {item.total_score.toFixed(1)}
                    </span>
                    <span className={`rounded-full px-3 py-1 text-xs font-semibold ${verdictTone(reviewItem?.verdict)}`}>
                      {reviewItem?.verdict ?? "未审查"}
                    </span>
                  </div>
                </div>
                <div className="mt-4 grid gap-3 md:grid-cols-2">
                  <div className="rounded-[16px] bg-paper p-4 text-sm leading-6 text-ink/65">
                    <p className="font-semibold text-ink">推荐理由</p>
                    <p className="mt-1">{String(item.rationale.summary ?? "来自规则评分排序。")}</p>
                  </div>
                  <div className="rounded-[16px] bg-paper p-4 text-sm leading-6 text-ink/65">
                    <p className="font-semibold text-ink">风险提示</p>
                    <p className="mt-1">{reviewItem?.risk_flags.length ? reviewItem.risk_flags.join("、") : "暂无主要风险标签"}</p>
                  </div>
                  <div className="rounded-[16px] bg-white p-4 text-sm leading-6 text-ink/65 ring-1 ring-ink/10">
                    <p className="font-semibold text-ink">数据员</p>
                    <p className="mt-1">{reviewNoteText(reviewItem, "数据员")}</p>
                  </div>
                  <div className="rounded-[16px] bg-white p-4 text-sm leading-6 text-ink/65 ring-1 ring-ink/10">
                    <p className="font-semibold text-ink">风控员</p>
                    <p className="mt-1">{reviewNoteText(reviewItem, "风控员")}</p>
                  </div>
                  <div className="rounded-[16px] bg-white p-4 text-sm leading-6 text-ink/65 ring-1 ring-ink/10">
                    <p className="font-semibold text-ink">反方</p>
                    <p className="mt-1">{reviewNoteText(reviewItem, "反方")}</p>
                  </div>
                  <div className="rounded-[16px] bg-white p-4 text-sm leading-6 text-ink/65 ring-1 ring-ink/10">
                    <p className="font-semibold text-ink">总结员</p>
                    <p className="mt-1">{reviewNoteText(reviewItem, "总结员")}</p>
                  </div>
                </div>
              </div>
            );
          })}
          {!screening.isLoading && (screening.data?.items ?? []).length === 0 ? (
            <div className="rounded-[18px] border border-dashed border-ink/20 p-5 text-sm text-ink/60">
              还没有筛选结果。先创建“基金筛选评分”模板，然后运行一次回测或在后台任务页点击“运行筛选评分”。
            </div>
          ) : null}
        </Panel>
      ) : null}
    </div>
  );
}

export default function StrategyLabPage() {
  return (
    <Suspense fallback={<div className="text-sm text-ink/60">正在加载策略实验室...</div>}>
      <StrategyLabClient />
    </Suspense>
  );
}
