"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useMemo } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from "recharts";

import { Panel } from "@/components/ui";
import { api } from "@/lib/api";
import { formatCurrency, formatDate, formatPercent } from "@/lib/format";
import type {
  EtfExitCredibility,
  EtfExitHyperopt,
  EtfOptimizedAllocation,
  EtfPortfolioBacktestDetail,
  EtfPortfolioBacktestLabelSummary,
  EtfPortfolioBacktestList,
  EtfStrategyComparison,
  EtfStrategyHealthcheck,
  ShortResearchObservationPortfolio
} from "@/lib/types";

function errorText(error: unknown) {
  if (typeof error === "object" && error !== null && "response" in error) {
    const response = (error as { response?: { data?: { detail?: string } } }).response;
    if (response?.data?.detail) {
      return response.data.detail;
    }
  }
  return error instanceof Error ? error.message : "操作失败";
}

function metricNumber(metrics: Record<string, unknown> | undefined, key: string) {
  const value = metrics?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function metricPercent(metrics: Record<string, unknown> | undefined, key: string) {
  const value = metricNumber(metrics, key);
  return value === null ? "暂无" : formatPercent(value * 100);
}

function metricInteger(metrics: Record<string, unknown> | undefined, key: string) {
  const value = metricNumber(metrics, key);
  return value === null ? "暂无" : String(Math.round(value));
}

function formatNullableRate(value: number | null | undefined) {
  return value === null || value === undefined ? "暂无" : formatPercent(value * 100);
}

function signalLabel(value: string) {
  const labels: Record<string, string> = {
    hard_stop: "硬止损",
    trailing_take_profit: "移动止盈",
    trend_weakening: "趋势转弱",
    take_profit_watch: "止盈观察",
    exit_watch: "退出观察"
  };
  return labels[value] ?? value;
}

function metadataString(metadata: Record<string, unknown>, key: string) {
  const value = metadata[key];
  return typeof value === "string" ? value : null;
}

function metadataNumber(metadata: Record<string, unknown>, key: string) {
  const value = metadata[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function formatDateTime(value: string | null | undefined) {
  if (!value) {
    return "暂无";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}

function backtestExecutionModel(data: EtfPortfolioBacktestDetail | undefined) {
  const model = data?.execution_model ?? data?.replay_contract?.execution_model;
  return typeof model === "string" ? model : "daily_close";
}

function backtestExecutionLabel(data: EtfPortfolioBacktestDetail | undefined) {
  return backtestExecutionModel(data) === "intraday_alert_v1" ? "盘中提醒执行回测" : "日线收盘模拟";
}

function evidenceContractText(status: string | undefined, summary: Record<string, unknown> | undefined) {
  if (!summary) {
    return "等待生成证据契约。";
  }
  const matched = summary.contract_matched;
  if (matched === true) {
    return "同源已验证：当前回测、标签验证和工作台策略使用同一证据契约。";
  }
  if (status === "旧口径结果" || status === "legacy") {
    return "旧口径结果：只能作为历史参考，不能证明当前工作台策略。";
  }
  return "等待更多同源证据。";
}

type LabelEvidencePoint = {
  sampleCount: number;
  medianReturn: number | null;
  winRate: number | null;
};

type LabelEvidenceRow = {
  label: string;
  entryTimingLabel: string;
  fiveDay?: LabelEvidencePoint;
  tenDay?: LabelEvidencePoint;
};

function formatEvidencePercent(value: number | null | undefined) {
  return value === null || value === undefined ? "暂无" : formatPercent(value * 100);
}

function buildLabelEvidenceRows(summaries: EtfPortfolioBacktestLabelSummary[]) {
  const rows = new Map<string, LabelEvidenceRow>();
  for (const item of summaries) {
    if (item.horizon_days !== 5 && item.horizon_days !== 10) {
      continue;
    }
    const key = `${item.label}::${item.entry_timing_label}`;
    const row = rows.get(key) ?? {
      label: item.label,
      entryTimingLabel: item.entry_timing_label
    };
    const point = {
      sampleCount: item.sample_count,
      medianReturn: item.median_return,
      winRate: item.win_rate
    };
    if (item.horizon_days === 5 && (!row.fiveDay || item.sample_count > row.fiveDay.sampleCount)) {
      row.fiveDay = point;
    }
    if (item.horizon_days === 10 && (!row.tenDay || item.sample_count > row.tenDay.sampleCount)) {
      row.tenDay = point;
    }
    rows.set(key, row);
  }
  return Array.from(rows.values()).sort((left, right) => {
    const rightSamples = right.tenDay?.sampleCount ?? right.fiveDay?.sampleCount ?? 0;
    const leftSamples = left.tenDay?.sampleCount ?? left.fiveDay?.sampleCount ?? 0;
    if (rightSamples !== leftSamples) {
      return rightSamples - leftSamples;
    }
    return `${left.label}${left.entryTimingLabel}`.localeCompare(`${right.label}${right.entryTimingLabel}`, "zh-CN");
  });
}

function labelEvidenceConclusion(row: LabelEvidenceRow) {
  const tenDay = row.tenDay;
  if (!tenDay?.sampleCount) {
    return "样本不足";
  }
  const median = tenDay.medianReturn;
  const winRate = tenDay.winRate;
  if (row.entryTimingLabel === "跌破等待" && median !== null && median !== undefined && median > 0) {
    return "历史反弹较多，风险也高";
  }
  if (median !== null && median !== undefined && winRate !== null && winRate !== undefined && median > 0 && winRate >= 0.55) {
    return "历史表现较好";
  }
  if ((median !== null && median !== undefined && median > 0) || (winRate !== null && winRate !== undefined && winRate >= 0.5)) {
    return "勉强可看";
  }
  if (median !== null && median !== undefined && winRate !== null && winRate !== undefined && median < 0 && winRate < 0.45) {
    return "不理想";
  }
  return "样本有限";
}

export default function EtfEvidencePage() {
  const queryClient = useQueryClient();

  const backtests = useQuery({
    queryKey: ["short-research", "etf-backtests"],
    queryFn: async () => (await api.get<EtfPortfolioBacktestList>("/api/short-research/etf-backtests?limit=1")).data
  });
  const latestBacktestId = backtests.data?.items[0]?.id ?? null;
  const backtestDetail = useQuery({
    queryKey: ["short-research", "etf-backtests", latestBacktestId],
    enabled: latestBacktestId !== null,
    queryFn: async () =>
      (await api.get<EtfPortfolioBacktestDetail>(`/api/short-research/etf-backtests/${latestBacktestId}`)).data
  });
  const strategyComparison = useQuery({
    queryKey: ["short-research", "etf-strategy-comparison", "latest"],
    queryFn: async () =>
      (await api.get<EtfStrategyComparison | null>("/api/short-research/etf-strategy-comparisons/latest")).data
  });
  const strategyHealthcheck = useQuery({
    queryKey: ["short-research", "etf-strategy-healthcheck", "latest"],
    queryFn: async () =>
      (await api.get<EtfStrategyHealthcheck | null>("/api/short-research/etf-strategy-healthcheck/latest")).data
  });
  const observationPortfolio = useQuery({
    queryKey: ["short-research", "observation-portfolio", "default"],
    queryFn: async () =>
      (
        await api.get<ShortResearchObservationPortfolio>(
          "/api/short-research/observation-portfolio?asset_type=etf&universe=default"
        )
      ).data
  });
  const optimizedAllocation = useQuery({
    queryKey: ["short-research", "etf-optimized-allocation", "latest"],
    queryFn: async () =>
      (await api.get<EtfOptimizedAllocation | null>("/api/short-research/etf-optimized-allocation/latest")).data
  });
  const exitHyperopt = useQuery({
    queryKey: ["short-research", "etf-exit-hyperopt", "latest"],
    queryFn: async () =>
      (await api.get<EtfExitHyperopt | null>("/api/short-research/etf-exit-hyperopt/latest")).data
  });
  const exitCredibility = useQuery({
    queryKey: ["short-research", "etf-exit-credibility", "latest"],
    queryFn: async () =>
      (
        await api.get<EtfExitCredibility | null>(
          "/api/short-research/etf-exit-credibility/latest?execution_model=intraday_alert"
        )
      ).data
  });

  const runBacktest = useMutation({
    mutationFn: async (executionModel: "daily_close" | "intraday_alert") =>
      (
        await api.post<EtfPortfolioBacktestDetail>("/api/short-research/etf-backtests", {
          days: 730,
          fee_rate: 0.001,
          max_assets: 500,
          execution_model: executionModel
        })
      ).data,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["short-research", "etf-backtests"] });
    }
  });
  const runComparison = useMutation({
    mutationFn: async () =>
      (
        await api.post<EtfStrategyComparison>("/api/short-research/etf-strategy-comparisons", {
          days: 730,
          fee_rate: 0.001,
          max_assets: 500
        })
      ).data,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["short-research", "etf-strategy-comparison"] });
    }
  });
  const runHealthcheck = useMutation({
    mutationFn: async () =>
      (await api.post<EtfStrategyHealthcheck>("/api/short-research/etf-strategy-healthcheck/run")).data,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["short-research", "etf-strategy-healthcheck"] });
    }
  });
  const runOptimizedAllocation = useMutation({
    mutationFn: async () =>
      (await api.post<EtfOptimizedAllocation>("/api/short-research/etf-optimized-allocation/run")).data,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["short-research", "etf-optimized-allocation"] }),
        queryClient.invalidateQueries({ queryKey: ["short-research", "observation-portfolio"] })
      ]);
    }
  });
  const runExitHyperopt = useMutation({
    mutationFn: async () =>
      (
        await api.post<EtfExitHyperopt>("/api/short-research/etf-exit-hyperopt/run", {
          days: 730,
          objective: "stability_first",
          execution_model: "intraday_alert",
          manual_delay_minutes: 3
        })
      ).data,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["short-research", "etf-exit-hyperopt"] });
    }
  });
  const runExitCredibility = useMutation({
    mutationFn: async () =>
      (
        await api.post<EtfExitCredibility>("/api/short-research/etf-exit-credibility/run", {
          days: 730,
          max_assets: 300,
          execution_model: "intraday_alert"
        })
      ).data,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["short-research", "etf-exit-credibility"] });
    }
  });

  const detail = backtestDetail.data;
  const isIntradayBacktest = backtestExecutionModel(detail) === "intraday_alert_v1";
  const optimized = observationPortfolio.data?.optimized_allocation ?? optimizedAllocation.data ?? null;
  const labelEvidenceRows = useMemo(() => buildLabelEvidenceRows(detail?.label_summaries ?? []), [detail?.label_summaries]);
  const hyperoptCoverageRaw = exitHyperopt.data?.summary.coverage ?? exitHyperopt.data?.summary.coverage_funnel;
  const hyperoptCoverage =
    typeof hyperoptCoverageRaw === "object" && hyperoptCoverageRaw !== null
      ? (hyperoptCoverageRaw as Record<string, unknown>)
      : {};

  return (
    <div className="space-y-5">
      <Panel>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">策略证据</p>
            <h2 className="mt-1 text-2xl font-semibold tracking-[-0.02em] text-ink">ETF 工作台策略证据</h2>
            <p className="mt-2 text-sm leading-6 text-ink/60">
              集中查看回测、标签事后表现、策略对照、体检和组合优化证据。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              className="rounded-[6px] border border-border bg-white px-3 py-2 text-sm font-semibold text-ink transition hover:border-ink/30 disabled:opacity-60"
              disabled={runBacktest.isPending}
              onClick={() => runBacktest.mutate("daily_close")}
            >
              日线回测
            </button>
            <button
              className="rounded-[6px] border border-border bg-white px-3 py-2 text-sm font-semibold text-ink transition hover:border-ink/30 disabled:opacity-60"
              disabled={runBacktest.isPending}
              onClick={() => runBacktest.mutate("intraday_alert")}
            >
              盘中回测
            </button>
            <button
              className="rounded-[6px] bg-ink px-3 py-2 text-sm font-semibold text-white transition hover:bg-ink/85 disabled:opacity-60"
              disabled={runComparison.isPending}
              onClick={() => runComparison.mutate()}
            >
              策略对照
            </button>
            <button
              className="rounded-[6px] bg-ink px-3 py-2 text-sm font-semibold text-white transition hover:bg-ink/85 disabled:opacity-60"
              disabled={runHealthcheck.isPending}
              onClick={() => runHealthcheck.mutate()}
            >
              策略体检
            </button>
            <button
              className="rounded-[6px] bg-ink px-3 py-2 text-sm font-semibold text-white transition hover:bg-ink/85 disabled:opacity-60"
              disabled={runExitHyperopt.isPending}
              onClick={() => runExitHyperopt.mutate()}
            >
              参数优化
            </button>
            <button
              className="rounded-[6px] bg-ink px-3 py-2 text-sm font-semibold text-white transition hover:bg-ink/85 disabled:opacity-60"
              disabled={runExitCredibility.isPending}
              onClick={() => runExitCredibility.mutate()}
            >
              退出信号验证
            </button>
          </div>
        </div>
        {[runBacktest, runComparison, runHealthcheck, runOptimizedAllocation, runExitHyperopt, runExitCredibility].map((mutation, index) =>
          mutation.isError ? (
            <p key={index} className="mt-3 rounded-[8px] border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
              {errorText(mutation.error)}
            </p>
          ) : null
        )}
      </Panel>

      <Panel>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-sm font-semibold text-ink">退出信号可信度</p>
            <p className="mt-1 text-xs text-ink/55">
              {exitCredibility.data
                ? `${exitCredibility.data.execution_model} · ${exitCredibility.data.evidence_status}`
                : "等待验证"}
            </p>
          </div>
          <span className="w-fit rounded-full bg-paper px-2.5 py-1 text-xs text-ink/60">
            {exitCredibility.data
              ? `${formatDate(exitCredibility.data.as_of_date)} · 样本 ${metricInteger(exitCredibility.data.summary, "event_count")}`
              : "暂无报告"}
          </span>
        </div>
        {exitCredibility.data ? (
          <>
            <div className="mt-4 grid gap-3 md:grid-cols-4">
              <EvidenceStat label="覆盖 ETF" value={metricInteger(exitCredibility.data.summary, "asset_count")} />
              <EvidenceStat label="理论事件" value={metricInteger(exitCredibility.data.summary, "event_count")} />
              <EvidenceStat label="已验证信号" value={metricInteger(exitCredibility.data.summary, "verified_signal_count")} />
              <EvidenceStat label="数据截止" value={formatDateTime(exitCredibility.data.data_cutoff)} />
            </div>
            {exitCredibility.data.insufficiency_reasons.length ? (
              <div className="mt-4 rounded-[8px] border border-dashed border-border bg-paper px-3 py-3 text-sm text-ink/60">
                {exitCredibility.data.insufficiency_reasons.join("；")}
              </div>
            ) : null}
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              {exitCredibility.data.items
                .filter((item) => item.group_type === "signal")
                .map((item) => (
                  <div key={item.id} className="rounded-[8px] border border-border bg-paper/40 p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <p className="text-sm font-semibold text-ink">{signalLabel(item.signal_type)}</p>
                        <p className="mt-1 text-xs text-ink/55">
                          {item.evidence_level} · 样本 {item.sample_count}
                        </p>
                      </div>
                      <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-ink/55">
                        {formatNullableRate(item.success_avoidance_rate)}
                      </span>
                    </div>
                    <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-ink/60">
                      <span>成功避险：{formatNullableRate(item.success_avoidance_rate)}</span>
                      <span>错杀率：{formatNullableRate(item.false_stop_rate)}</span>
                      <span>卖飞率：{formatNullableRate(item.sold_too_early_rate)}</span>
                      <span>
                        平均后续收益：{item.avg_forward_return === null ? "暂无" : formatPercent(item.avg_forward_return * 100)}
                      </span>
                      <span>
                        平均少亏：{item.avg_avoided_drawdown === null ? "暂无" : formatPercent(item.avg_avoided_drawdown * 100)}
                      </span>
                      <span>
                        平均错过上涨：{item.avg_missed_upside === null ? "暂无" : formatPercent(item.avg_missed_upside * 100)}
                      </span>
                    </div>
                    {item.events.length ? (
                      <div className="mt-3 space-y-1 border-t border-border pt-3 text-xs text-ink/50">
                        {item.events.slice(0, 2).map((event) => (
                          <p key={event.id}>
                            {event.etf_name ?? event.etf_code} · {formatDate(event.signal_date)} · {event.outcome}
                          </p>
                        ))}
                      </div>
                    ) : null}
                  </div>
                ))}
            </div>
          </>
        ) : (
          <p className="mt-4 rounded-[8px] border border-dashed border-border bg-paper px-3 py-4 text-sm text-ink/55">
            暂无退出信号可信度报告。可以点击“退出信号验证”运行一次，或等待服务器夜间任务。
          </p>
        )}
      </Panel>

      <div className="grid gap-5 xl:grid-cols-[1.35fr_0.9fr]">
        <Panel>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <p className="text-sm font-semibold text-ink">回测证据</p>
              <p className="mt-1 text-xs text-ink/55">{backtestExecutionLabel(detail)}</p>
            </div>
            <div className="flex flex-wrap gap-2 text-xs text-ink/55">
              <span className="rounded-full bg-paper px-2.5 py-1">
                {detail?.evidence_status ?? "等待验证"}
              </span>
              <span className="rounded-full bg-paper px-2.5 py-1">
                {detail ? `${formatDate(detail.start_date)} - ${formatDate(detail.end_date)}` : "暂无区间"}
              </span>
            </div>
          </div>
          <p className="mt-3 rounded-[8px] border border-border bg-paper px-3 py-2 text-xs leading-5 text-ink/60">
            {evidenceContractText(detail?.evidence_status, detail?.evidence_summary)}
          </p>
          <div className="mt-4 grid gap-3 md:grid-cols-4">
            <EvidenceStat label="历史收益" value={metricPercent(detail?.metrics, "cumulative_return")} />
            <EvidenceStat label="最大回撤" value={metricPercent(detail?.metrics, "max_drawdown")} />
            <EvidenceStat label="胜率" value={metricPercent(detail?.metrics, "win_rate")} />
            <EvidenceStat label="交易次数" value={metricInteger(detail?.metrics, "trade_count")} />
          </div>
          <div className="mt-5 h-72 rounded-[10px] border border-border bg-white p-3">
            {detail?.equity_curve.length ? (
              <ResponsiveContainer width="100%" height="100%">
                <LineChart
                  data={detail.equity_curve.map((point) => ({
                    label: formatDate(point.date),
                    equity: point.equity,
                    benchmark: point.benchmark_equity
                  }))}
                >
                  <CartesianGrid stroke="#e6e6e6" vertical={false} />
                  <XAxis dataKey="label" tickLine={false} axisLine={false} minTickGap={28} />
                  <YAxis tickLine={false} axisLine={false} width={64} />
                  <Tooltip formatter={(value) => formatCurrency(Number(value))} />
                  <Line type="monotone" dataKey="equity" name="策略权益" stroke="#111" dot={false} strokeWidth={2} />
                  <Line type="monotone" dataKey="benchmark" name="宽基对照" stroke="#777" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-ink/45">暂无回测曲线。</div>
            )}
          </div>
        </Panel>

        <Panel>
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-ink">标签组合有效性</p>
              <p className="mt-1 text-xs text-ink/55">按买入观察和今日买点分组，展示历史前瞻表现。</p>
            </div>
            <Link href="/short-term" className="rounded-[6px] border border-border px-3 py-2 text-xs font-semibold text-ink/70">
              回到短线研究
            </Link>
          </div>
          {labelEvidenceRows.length ? (
            <div className="mt-4 overflow-x-auto rounded-[10px] border border-border">
              <table className="min-w-[760px] w-full border-collapse text-left text-xs">
                <thead className="bg-paper text-ink/55">
                  <tr>
                    <th className="px-3 py-2 font-semibold">标签组合</th>
                    <th className="px-3 py-2 font-semibold">5日中位收益</th>
                    <th className="px-3 py-2 font-semibold">5日胜率</th>
                    <th className="px-3 py-2 font-semibold">10日中位收益</th>
                    <th className="px-3 py-2 font-semibold">10日胜率</th>
                    <th className="px-3 py-2 font-semibold">样本数</th>
                    <th className="px-3 py-2 font-semibold">结论</th>
                  </tr>
                </thead>
                <tbody>
                  {labelEvidenceRows.map((row) => (
                    <tr key={`${row.label}-${row.entryTimingLabel}`} className="border-t border-border align-top">
                      <td className="px-3 py-2 font-semibold text-ink">
                        {row.label} + {row.entryTimingLabel}
                      </td>
                      <td className="px-3 py-2 text-ink/65">{formatEvidencePercent(row.fiveDay?.medianReturn)}</td>
                      <td className="px-3 py-2 text-ink/65">{formatEvidencePercent(row.fiveDay?.winRate)}</td>
                      <td className="px-3 py-2 text-ink/65">{formatEvidencePercent(row.tenDay?.medianReturn)}</td>
                      <td className="px-3 py-2 text-ink/65">{formatEvidencePercent(row.tenDay?.winRate)}</td>
                      <td className="px-3 py-2 text-ink/65">
                        5日 {row.fiveDay?.sampleCount ?? 0} / 10日 {row.tenDay?.sampleCount ?? 0}
                      </td>
                      <td className="px-3 py-2 text-ink/75">{labelEvidenceConclusion(row)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="mt-4 rounded-[8px] border border-dashed border-border bg-paper px-3 py-4 text-sm text-ink/55">
                暂无标签样本。先运行回测或等待标签事后验证完成。
            </p>
          )}
          {labelEvidenceRows.length ? (
            <p className="mt-3 text-xs leading-5 text-ink/50">
              标签表现只用于验证和降权参考，不等于买入指令。
            </p>
          ) : null}
        </Panel>
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <Panel>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p className="text-sm font-semibold text-ink">策略对照</p>
              <p className="mt-1 text-xs text-ink/55">
                {strategyComparison.data
                  ? `${formatDate(strategyComparison.data.start_date)} - ${formatDate(strategyComparison.data.end_date)}`
                  : "暂无结果"}
              </p>
            </div>
            <span className="w-fit rounded-full bg-paper px-2.5 py-1 text-xs text-ink/60">
              最优历史项：{strategyComparison.data?.best_strategy ?? "暂无"}
            </span>
          </div>
          <div className="mt-4 grid gap-3">
            {strategyComparison.data?.strategies.map((strategy) => (
              <div key={strategy.strategy_key} className="rounded-[8px] border border-border bg-paper/40 p-3">
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm font-semibold text-ink">{strategy.strategy_label}</p>
                  <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-ink/55">
                    {strategy.strategy_key}
                  </span>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-ink/60">
                  <span>累计收益：{metricPercent(strategy.metrics, "cumulative_return")}</span>
                  <span>最大回撤：{metricPercent(strategy.metrics, "max_drawdown")}</span>
                  <span>波动率：{metricPercent(strategy.metrics, "volatility")}</span>
                  <span>交易次数：{metricInteger(strategy.metrics, "trade_count")}</span>
                </div>
              </div>
            ))}
            {!strategyComparison.data ? (
              <p className="rounded-[8px] border border-dashed border-border bg-paper px-3 py-4 text-sm text-ink/55">
                暂无策略对照结果。
              </p>
            ) : null}
          </div>
        </Panel>

        <Panel>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p className="text-sm font-semibold text-ink">策略体检</p>
              <p className="mt-1 text-xs text-ink/55">体检日：{formatDate(strategyHealthcheck.data?.as_of_date)}</p>
            </div>
            <span className="w-fit rounded-full bg-paper px-2.5 py-1 text-xs text-ink/60">
              {strategyHealthcheck.data?.evidence_status ?? "等待验证"}
            </span>
          </div>
          {strategyHealthcheck.data ? (
            <>
              <p className="mt-4 rounded-[8px] bg-paper px-3 py-2 text-sm font-semibold text-ink">
                {strategyHealthcheck.data.conclusion}
              </p>
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {strategyHealthcheck.data.items.slice(0, 6).map((item) => (
                  <div key={`${item.item_type}-${item.item_key}`} className="rounded-[8px] border border-border bg-paper/40 p-3 text-xs leading-5 text-ink/60">
                    <p className="text-sm font-semibold text-ink">{item.item_key}</p>
                    <p>结论：{item.conclusion}</p>
                    <p>样本：{item.sample_count}</p>
                    <p>胜率：{item.win_rate === null ? "暂无" : formatPercent(item.win_rate * 100)}</p>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="mt-4 rounded-[8px] border border-dashed border-border bg-paper px-3 py-4 text-sm text-ink/55">
              暂无策略体检结果。
            </p>
          )}
        </Panel>
      </div>

      <Panel>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-sm font-semibold text-ink">参数优化证据</p>
            <p className="mt-1 text-xs text-ink/55">
              当前邮件规则不自动改变；候选参数必须人工确认后才可能进入生效规则。
            </p>
          </div>
          <span className="w-fit rounded-full bg-paper px-2.5 py-1 text-xs text-ink/60">
            {exitHyperopt.data ? `${formatDate(exitHyperopt.data.as_of_date)} · ${exitHyperopt.data.objective}` : "暂无结果"}
          </span>
        </div>
        {exitHyperopt.data ? (
          <>
            <div className="mt-4 grid gap-3 md:grid-cols-4">
              <EvidenceStat label="分桶数量" value={metricInteger(exitHyperopt.data.summary, "bucket_count")} />
              <EvidenceStat label="候选数量" value={metricInteger(exitHyperopt.data.summary, "candidate_count")} />
              <EvidenceStat label="证据不足" value={metricInteger(exitHyperopt.data.summary, "evidence_insufficient_count")} />
              <EvidenceStat label="数据截止" value={formatDateTime(exitHyperopt.data.data_cutoff)} />
            </div>
            <div className="mt-3 grid gap-3 md:grid-cols-3">
              <EvidenceStat label="执行模型" value={exitHyperopt.data.execution_model ?? "日线收盘"} />
              <EvidenceStat
                label="覆盖口径"
                value={exitHyperopt.data.summary.sampled === true ? "抽样结果" : "全量候选"}
              />
              <EvidenceStat label="手动延迟" value={`${metricInteger(exitHyperopt.data.summary, "manual_delay_minutes")} 分钟`} />
            </div>
            <div className="mt-3 grid gap-3 md:grid-cols-4">
              <EvidenceStat label="全市场 ETF" value={metricInteger(hyperoptCoverage, "all_etf_count")} />
              <EvidenceStat label="可优化候选" value={metricInteger(hyperoptCoverage, "eligible_count")} />
              <EvidenceStat label="盘中样本足够" value={metricInteger(hyperoptCoverage, "enough_intraday_history_count")} />
              <EvidenceStat label="完成优化" value={metricInteger(hyperoptCoverage, "final_optimized_count")} />
            </div>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <EvidenceStat label="校准版本" value={exitHyperopt.data.calibration_rule_version ?? "旧口径"} />
              <EvidenceStat
                label="合同 hash"
                value={exitHyperopt.data.contract_hash ? exitHyperopt.data.contract_hash.slice(0, 10) : "暂无"}
              />
            </div>
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              {exitHyperopt.data.items.slice(0, 6).map((item) => (
                <div key={item.id} className="rounded-[8px] border border-border bg-paper/40 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-ink">
                        {item.bucket_type} / {item.bucket_key}
                      </p>
                      <p className="mt-1 text-xs text-ink/55">
                        {item.conclusion} · 分数 {item.score.toFixed(1)}
                      </p>
                    </div>
                    <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-ink/55">
                      {item.status}
                    </span>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-ink/60">
                    <span>样本外收益：{metricPercent(item.out_of_sample_metrics, "total_return")}</span>
                    <span>样本外回撤：{metricPercent(item.out_of_sample_metrics, "max_drawdown")}</span>
                    <span>胜率：{metricPercent(item.out_of_sample_metrics, "win_rate")}</span>
                    <span>提醒次数：{metricInteger(item.out_of_sample_metrics, "alert_count")}</span>
                    <span>交易次数：{item.trade_count}</span>
                    <span>样本：{item.sample_count}</span>
                    <span>滚动稳定率：{metricPercent(item.rolling_metrics, "stable_window_rate")}</span>
                    <span>最差窗口收益：{metricPercent(item.rolling_metrics, "worst_window_return")}</span>
                    <span>错杀率：{metricPercent(item.out_of_sample_metrics, "missed_upside_rate")}</span>
                    <span>保护率：{metricPercent(item.out_of_sample_metrics, "protected_exit_rate")}</span>
                    <span>对默认规则：{item.baseline_comparison.beats_baseline === true ? "更好" : "未胜出"}</span>
                    <span>覆盖：{item.coverage_status ?? "旧口径"}</span>
                    <span>未成交：{metricInteger(item.out_of_sample_metrics, "unfilled_count")}</span>
                    <span>延迟：{item.manual_delay_minutes ?? "暂无"} 分钟</span>
                    <span>置信度：{metadataString(item.confidence, "level") ?? "暂无"}</span>
                    <span>数据源：{item.source_reliability ?? "旧口径"}</span>
                  </div>
                  {item.rejection_reason ? (
                    <p className="mt-2 rounded-[6px] bg-white px-2 py-1 text-xs text-ink/55">
                      未采用原因：{item.rejection_reason}
                    </p>
                  ) : null}
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="mt-4 rounded-[8px] border border-dashed border-border bg-paper px-3 py-4 text-sm text-ink/55">
            暂无参数优化证据。可以点击“参数优化”运行一次，或等待服务器夜间任务。
          </p>
        )}
      </Panel>

      <Panel>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="text-sm font-semibold text-ink">组合配置证据</p>
            <p className="mt-1 text-xs text-ink/55">
              模式：{observationPortfolio.data?.portfolio_mode ?? "暂无"} · 权重合计{" "}
              {observationPortfolio.data ? formatPercent((observationPortfolio.data.weight_sum ?? 0) * 100) : "暂无"}
            </p>
          </div>
          <button
            type="button"
            className="w-fit rounded-[6px] bg-ink px-3 py-2 text-sm font-semibold text-white transition hover:bg-ink/85 disabled:opacity-60"
            disabled={runOptimizedAllocation.isPending}
            onClick={() => runOptimizedAllocation.mutate()}
          >
            {runOptimizedAllocation.isPending ? "正在优化..." : "运行优化对照"}
          </button>
        </div>
        <div className="mt-4 grid gap-3 lg:grid-cols-3">
          {optimized?.methods.length ? (
            optimized.methods.map((method) => (
              <div key={method.method} className="rounded-[8px] border border-border bg-paper/40 p-3">
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm font-semibold text-ink">{method.label}</p>
                  <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-ink/55">
                    {formatPercent(method.weight_sum * 100)}
                  </span>
                </div>
                <div className="mt-3 space-y-2">
                  {method.items.slice(0, 5).map((item) => (
                    <div key={item.code} className="flex items-center justify-between gap-3 text-xs text-ink/60">
                      <span className="truncate">{item.name}</span>
                      <span className="font-semibold text-ink">{formatPercent(item.target_weight * 100)}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))
          ) : (
            <p className="rounded-[8px] border border-dashed border-border bg-paper px-3 py-4 text-sm text-ink/55 lg:col-span-3">
              {optimized?.unavailable_reason ?? "暂无优化组合对照。"}
            </p>
          )}
        </div>
      </Panel>

      {detail?.trades.length ? (
        <Panel>
          <p className="text-sm font-semibold text-ink">最近模拟交易</p>
          <div className="mt-3 grid gap-2 text-xs text-ink/60">
            {detail.trades.slice(0, 8).map((trade) => {
              const signalTime = metadataString(trade.metadata, "signal_time");
              const executionTime = metadataString(trade.metadata, "execution_time");
              const signalPrice = metadataNumber(trade.metadata, "signal_price");
              const executionPrice = metadataNumber(trade.metadata, "execution_price");
              const delay = metadataNumber(trade.metadata, "execution_delay_minutes");
              return (
                <div key={trade.id} className="rounded-[8px] bg-paper px-3 py-2">
                  <p>
                    {formatDate(trade.trade_date)} · {trade.side === "buy" ? "买入" : "卖出"} {trade.etf_name}
                    {" "}{formatCurrency(trade.amount)} · {trade.reason}
                  </p>
                  {isIntradayBacktest && signalTime ? (
                    <p className="mt-1 text-ink/45">
                      提醒 {formatDateTime(signalTime)}
                      {signalPrice === null ? "" : ` @ ${signalPrice.toFixed(4)}`}
                      {" → "}
                      成交 {formatDateTime(executionTime)}
                      {executionPrice === null ? "" : ` @ ${executionPrice.toFixed(4)}`}
                      {delay === null ? "" : `，延迟 ${delay} 分钟`}
                    </p>
                  ) : null}
                </div>
              );
            })}
          </div>
        </Panel>
      ) : null}
    </div>
  );
}

function EvidenceStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[10px] border border-border bg-white px-4 py-3">
      <p className="text-xs text-ink/45">{label}</p>
      <p className="mt-1 text-lg font-semibold text-ink">{value}</p>
    </div>
  );
}
