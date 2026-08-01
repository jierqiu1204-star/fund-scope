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
  EtfEvidenceOverview,
  EtfEvidenceSurface,
  EtfLeaderTacticsEvidence,
  EtfExitHyperopt,
  EtfOptimizedAllocation,
  EtfPortfolioBacktestDetail,
  EtfPortfolioBacktestLabelSummary,
  EtfPortfolioBacktestList,
  EtfStrategyComparison,
  EtfStrategyHealthcheck,
  JobRun,
  ShortResearchObservationPortfolio
} from "@/lib/types";

function errorText(error: unknown) {
  if (typeof error === "object" && error !== null && "response" in error) {
    const response = (error as { response?: { data?: { detail?: string } } })
      .response;
    if (response?.data?.detail) {
      return response.data.detail;
    }
  }
  return error instanceof Error ? error.message : "操作失败";
}

function metricNumber(
  metrics: Record<string, unknown> | undefined,
  key: string
) {
  const value = metrics?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function metricPercent(
  metrics: Record<string, unknown> | undefined,
  key: string
) {
  const value = metricNumber(metrics, key);
  return value === null ? "暂无" : formatPercent(value * 100);
}

function metricInteger(
  metrics: Record<string, unknown> | undefined,
  key: string
) {
  const value = metricNumber(metrics, key);
  return value === null ? "暂无" : String(Math.round(value));
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function asRecordArray(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.flatMap((item) => {
        const record = asRecord(item);
        return record ? [record] : [];
      })
    : [];
}

function formatNullableRate(value: number | null | undefined) {
  return value === null || value === undefined
    ? "暂无"
    : formatPercent(value * 100);
}

function signalLabel(value: string) {
  const labels: Record<string, string> = {
    hard_stop: "硬止损",
    trailing_take_profit: "移动止盈",
    trend_weakening: "趋势警戒",
    confirmed_trend_weakening: "确认趋势转弱",
    take_profit_watch: "止盈观察",
    exit_watch: "退出观察"
  };
  return labels[value] ?? value;
}

function metadataString(metadata: Record<string, unknown>, key: string) {
  const value = metadata[key];
  return typeof value === "string" ? value : null;
}

function policyLevelLabel(value: string | null) {
  if (value === "high") {
    return "高证据";
  }
  if (value === "medium") {
    return "中等证据";
  }
  if (value === "low") {
    return "样本不足/仅供观察";
  }
  return value ?? "暂无";
}

function policyStatusLabel(value: string | null | undefined) {
  const labels: Record<string, string> = {
    approved_live: "已批准生效",
    research_only_verified: "研究证据",
    candidate_research_only: "候选研究",
    sample_insufficient: "样本不足",
    not_current_contract: "非当前口径",
    verified: "已验证"
  };
  return labels[value ?? ""] ?? value ?? "旧口径";
}

function policyGroupDescription(value: string | null | undefined) {
  const descriptions: Record<string, string> = {
    insurance_stop: "止损线主要检验是否减少继续下探，不按“卖后一定下跌”评价。",
    profit_protection: "止盈线主要检验是否保护已有浮盈，同时观察卖飞成本。",
    trend_guard: "趋势警戒默认只是风控辅助，不单独作为卖出指令。",
    portfolio_exit: "退出观察主要检验组合或标签退化后的降暴露效果。",
    research_only: "旧口径或候选证据，只能作为研究参考。"
  };
  return (
    descriptions[value ?? ""] ?? "候选证据仅供研究，不自动影响实时邮件规则。"
  );
}

type KpiMetric = {
  key: string;
  label: string;
  value: number | null;
  format?: string;
};

function kpiMetrics(summary: Record<string, unknown> | undefined) {
  const metrics = summary?.metrics;
  return Array.isArray(metrics) ? (metrics as KpiMetric[]) : [];
}

function kpiText(metric: KpiMetric) {
  if (typeof metric.value !== "number" || !Number.isFinite(metric.value)) {
    return "暂无";
  }
  return metric.format === "percent"
    ? formatPercent(metric.value * 100)
    : String(metric.value);
}

function groupedCredibilityItems(items: EtfExitCredibility["items"]) {
  const groups = new Map<string, EtfExitCredibility["items"]>();
  for (const item of items.filter((entry) => entry.group_type === "signal")) {
    const key = item.policy_class ?? "research_only";
    groups.set(key, [...(groups.get(key) ?? []), item]);
  }
  return Array.from(groups.entries()).map(([policyClass, groupItems]) => ({
    policyClass,
    label: groupItems[0]?.policy_class_label ?? policyClass,
    items: groupItems
  }));
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
  return backtestExecutionModel(data) === "intraday_alert_v1"
    ? "盘中提醒执行回测"
    : "日线收盘模拟";
}

function backtestEvidenceSampleText(
  status: string | undefined,
  sampleCount: number | null | undefined
) {
  if (status === "legacy_diagnostic") {
    return "旧口径诊断样本，不计入当前结论";
  }
  return status === "available" &&
    sampleCount !== null &&
    sampleCount !== undefined
    ? `独立样本 ${sampleCount}`
    : "尚未生成独立样本";
}

function backtestFillFieldLabel(value: string | undefined) {
  const labels: Record<string, string> = {
    adjusted_open: "次日复权开盘",
    legacy_daily_close: "旧日线收盘口径",
    stored_intraday_quote_after_fixed_delay: "固定延迟后的已存盘中快照"
  };
  return labels[value ?? ""] ?? value ?? "暂无";
}

function evidenceContractText(
  status: string | undefined,
  summary: Record<string, unknown> | undefined
) {
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

const CURRENT_EVIDENCE_STATUS = "同源已验证";

function visibleHealthcheckItems(
  data: EtfStrategyHealthcheck | null | undefined
) {
  if (!data || data.evidence_status !== CURRENT_EVIDENCE_STATUS) {
    return [];
  }
  return data.items.filter((item) => item.sample_count > 0);
}

function healthcheckEvidenceIssue(
  data: EtfStrategyHealthcheck | null | undefined
) {
  if (!data) {
    return null;
  }
  const issue = data.summary.evidence_issue;
  if (typeof issue === "string" && issue.trim()) {
    return issue;
  }
  if (data.evidence_status !== CURRENT_EVIDENCE_STATUS) {
    return "当前体检是旧口径，不能证明当前标签；请重新生成同源证据。";
  }
  if (visibleHealthcheckItems(data).length === 0) {
    return "缺少可用标签回放样本，请先运行标签历史回放或策略回测。";
  }
  return null;
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
  return value === null || value === undefined
    ? "暂无"
    : formatPercent(value * 100);
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
    if (
      item.horizon_days === 5 &&
      (!row.fiveDay || item.sample_count > row.fiveDay.sampleCount)
    ) {
      row.fiveDay = point;
    }
    if (
      item.horizon_days === 10 &&
      (!row.tenDay || item.sample_count > row.tenDay.sampleCount)
    ) {
      row.tenDay = point;
    }
    rows.set(key, row);
  }
  return Array.from(rows.values()).sort((left, right) => {
    const rightSamples =
      right.tenDay?.sampleCount ?? right.fiveDay?.sampleCount ?? 0;
    const leftSamples =
      left.tenDay?.sampleCount ?? left.fiveDay?.sampleCount ?? 0;
    if (rightSamples !== leftSamples) {
      return rightSamples - leftSamples;
    }
    return `${left.label}${left.entryTimingLabel}`.localeCompare(
      `${right.label}${right.entryTimingLabel}`,
      "zh-CN"
    );
  });
}

function labelEvidenceConclusion(row: LabelEvidenceRow) {
  const tenDay = row.tenDay;
  if (!tenDay?.sampleCount) {
    return "样本不足";
  }
  const median = tenDay.medianReturn;
  const winRate = tenDay.winRate;
  if (
    row.entryTimingLabel === "跌破等待" &&
    median !== null &&
    median !== undefined &&
    median > 0
  ) {
    return "历史反弹较多，风险也高";
  }
  if (
    median !== null &&
    median !== undefined &&
    winRate !== null &&
    winRate !== undefined &&
    median > 0 &&
    winRate >= 0.55
  ) {
    return "历史表现较好";
  }
  if (
    (median !== null && median !== undefined && median > 0) ||
    (winRate !== null && winRate !== undefined && winRate >= 0.5)
  ) {
    return "勉强可看";
  }
  if (
    median !== null &&
    median !== undefined &&
    winRate !== null &&
    winRate !== undefined &&
    median < 0 &&
    winRate < 0.45
  ) {
    return "不理想";
  }
  return "样本有限";
}

const ETF_EVIDENCE_SURFACES = [
  ["production_ranking", "正式 V3 榜单"],
  ["research_replay", "历史 research replay"],
  ["policy_shadow", "Policy shadow"],
  ["live_notification", "真实邮件结果"],
  ["provider_delivery", "服务商送达"],
  ["confirmed_execution", "用户确认成交"]
] as const;

function evidenceAvailabilityLabel(surface: EtfEvidenceSurface) {
  if (surface.status === "available") {
    return "证据可用";
  }
  if (surface.status === "legacy") {
    return "版本不一致";
  }
  const reason = surface.unavailable_reason ?? "";
  if (
    reason.includes("independent_dates") ||
    reason.includes("eligible_sessions") ||
    reason.includes("walk_forward")
  ) {
    return "样本不足";
  }
  if (
    reason.includes("coverage") ||
    reason.includes("point_in_time_universe") ||
    reason.includes("adjusted_price")
  ) {
    return "覆盖不足";
  }
  if (reason.includes("future_window") || reason.includes("entry_or_exit")) {
    return "等待未来窗口";
  }
  if (reason.includes("incompatible") || reason.includes("legacy")) {
    return "版本不一致";
  }
  return "暂无真实证据";
}

function evidenceMetricText(surface: EtfEvidenceSurface) {
  const metric = surface.primary_metric;
  if (!metric || typeof metric.value !== "number") {
    return evidenceAvailabilityLabel(surface);
  }
  return `${formatPercent(metric.value * 100)} · 样本 ${metric.sample_count ?? 0}`;
}

function evidenceCoverageText(surface: EtfEvidenceSurface) {
  const coverage = Object.entries(surface.coverage)
    .filter(([, value]) => typeof value === "number" && Number.isFinite(value))
    .slice(0, 3)
    .map(([key, value]) => {
      const number = value as number;
      return key.endsWith("_ratio") || key === "coverage_ratio"
        ? `${key} ${formatPercent(number * 100)}`
        : `${key} ${number}`;
    });
  return coverage.length ? coverage.join(" · ") : "未提供可核验证覆盖";
}

function EtfEvidenceSurfaceCard({
  title,
  surface
}: {
  title: string;
  surface: EtfEvidenceSurface;
}) {
  return (
    <section
      className="rounded-[10px] border border-border bg-white p-4"
      data-evidence-surface={title}
    >
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-semibold text-ink">{title}</p>
          <p className="mt-1 text-xs text-ink/50">
            {surface.ranking_source_kind ?? "非排名证据"} ·{" "}
            {surface.policy_mode ?? "无 policy"}
          </p>
        </div>
        <span className="rounded-full bg-paper px-2.5 py-1 text-[11px] font-semibold text-ink/60">
          {evidenceAvailabilityLabel(surface)}
        </span>
      </div>
      <p className="mt-3 text-lg font-semibold text-ink">
        {evidenceMetricText(surface)}
      </p>
      <p className="mt-2 break-words text-xs leading-5 text-ink/55">
        {evidenceCoverageText(surface)}
      </p>
      <div className="mt-3 space-y-1 border-t border-border pt-3 text-xs text-ink/50">
        <p>数据截止：{formatDateTime(surface.data_cutoff)}</p>
        <p>排除：{surface.exclusions.count}</p>
        <p>
          证据清单：
          {surface.manifest_hash
            ? `${surface.manifest_hash.slice(0, 12)}…`
            : "暂无"}
        </p>
        {surface.notification_provenance ? (
          <p>通知 provenance：{surface.notification_provenance}</p>
        ) : null}
        {surface.execution_provenance ? (
          <p>执行 provenance：{surface.execution_provenance}</p>
        ) : null}
      </div>
    </section>
  );
}

function leaderStatusLabel(status: EtfLeaderTacticsEvidence["status"]) {
  const labels: Record<EtfLeaderTacticsEvidence["status"], string> = {
    insufficient_data: "证据不足",
    unconfirmed: "未确认",
    rejected: "已否决",
    eligible_for_v4_proposal: "可提出 V4 变更"
  };
  return labels[status];
}

function leaderObservationStateLabel(
  state: NonNullable<EtfLeaderTacticsEvidence["observation_state"]>
) {
  const labels: Record<
    NonNullable<EtfLeaderTacticsEvidence["observation_state"]>,
    string
  > = {
    not_started: "尚未开始",
    partial: "分页积累中",
    observing: "持续观察中",
    blocked: "观察受阻"
  };
  return labels[state];
}

function leaderCount(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? Math.floor(value)
    : 0;
}

function leaderEvidenceValue(key: string, value: unknown): string {
  if (typeof value === "number" && Number.isFinite(value)) {
    return key.includes("ratio") ||
      key.includes("coverage") ||
      key.includes("return") ||
      key.includes("excess")
      ? formatPercent(value * 100)
      : String(Math.round(value * 1000) / 1000);
  }
  if (typeof value === "boolean") {
    return value ? "是" : "否";
  }
  if (typeof value === "string") {
    return value;
  }
  if (Array.isArray(value)) {
    return value.length ? value.slice(0, 5).map(String).join("、") : "暂无";
  }
  const record = asRecord(value);
  if (record) {
    const text = Object.entries(record)
      .slice(0, 5)
      .map(
        ([nestedKey, nestedValue]) =>
          `${nestedKey}=${leaderEvidenceValue(nestedKey, nestedValue)}`
      )
      .join(" · ");
    return text || "暂无";
  }
  return "暂无";
}

function LeaderTacticsEvidencePanel({
  evidence
}: {
  evidence: EtfLeaderTacticsEvidence;
}) {
  const articles = asRecordArray(evidence.hypothesis_registry.articles);
  const statements = asRecordArray(evidence.hypothesis_registry.statements);
  const candidates = asRecordArray(evidence.candidate_registry.candidates);
  const observationState = evidence.observation_state ?? "not_started";
  const observationCounts = evidence.observation_counts;
  const currentObservations = (evidence.current_observations ?? []).slice(0, 20);
  const pendingOutcomes = (evidence.pending_outcomes ?? []).slice(0, 20);
  const outcomeCounts = observationCounts?.outcomes_by_horizon ?? [];
  const eligiblePitSessions = leaderCount(
    observationCounts?.eligible_pit_sessions
  );
  const materializedPitSessions = leaderCount(
    observationCounts?.materialized_pit_sessions
  );
  const requiredPitSessions = leaderCount(
    observationCounts?.required_pit_sessions
  );
  const currentInputAssetCount = leaderCount(
    observationCounts?.current_input_asset_count
  );
  const currentAvailableObservationCount = leaderCount(
    observationCounts?.current_available_observation_count
  );
  const currentQualifyingObservationCount = leaderCount(
    observationCounts?.current_qualifying_observation_count
  );
  const pendingOutcomeCount = leaderCount(
    observationCounts?.pending_outcome_count
  );
  const maturedOutcomeCount = leaderCount(
    observationCounts?.matured_outcome_count
  );
  const independentPrimaryDateCount = leaderCount(
    observationCounts?.independent_primary_date_count
  );
  const requiredPrimaryDateCount = leaderCount(
    observationCounts?.required_primary_date_count
  );
  const completedWalkForwardFoldCount = leaderCount(
    observationCounts?.completed_walk_forward_fold_count
  );
  const requiredWalkForwardFoldCount = leaderCount(
    observationCounts?.required_walk_forward_fold_count
  );
  const observationHasMore =
    observationCounts?.current_observations_truncated === true ||
    currentQualifyingObservationCount > currentObservations.length;
  const observationDataCutoff =
    evidence.observation_data_cutoff ?? evidence.data_cutoff;
  const observationManifestHash =
    evidence.observation_manifest_hash ?? evidence.manifest_hash;
  const diagnostics = [
    [
      "残差重叠",
      evidence.diagnostics.residual_overlap ??
        evidence.diagnostics.factor_overlap
    ],
    [
      "集中度",
      evidence.diagnostics.concentration ??
        evidence.diagnostics.maximum_concentration
    ],
    [
      "市场状态稳定性",
      evidence.diagnostics.regime_stability ??
        evidence.diagnostics.regime_slices
    ]
  ] as const;
  const coverageRows = Object.entries(evidence.coverage).slice(0, 8);
  const exclusionRows = Object.entries(evidence.exclusion_counts)
    .filter(([, count]) => count > 0)
    .sort((left, right) => right[1] - left[1])
    .slice(0, 8);

  return (
    <Panel>
      <section
        data-evidence-family="leader-tactics-shadow"
        data-research-only="true"
      >
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-sm font-semibold text-ink">
              龙头战术透明代理（研究）
            </p>
            <p className="mt-1 max-w-4xl text-xs leading-5 text-ink/55">
              这是面向 ETF
              的可复现透明代理，不等于原作者专有信号，也不代表作者背书、正式榜单或收益保证。
            </p>
          </div>
          <span className="w-fit rounded-full bg-paper px-2.5 py-1 text-xs font-semibold text-ink/60">
            {leaderStatusLabel(evidence.status)}
          </span>
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <EvidenceStat label="排名来源" value={evidence.ranking_source_kind} />
          <EvidenceStat label="Policy" value={evidence.policy_mode} />
          <EvidenceStat
            label="通知 provenance"
            value={evidence.notification_provenance}
          />
          <EvidenceStat
            label="执行 provenance"
            value={evidence.execution_provenance}
          />
        </div>
        <p className="mt-3 rounded-[8px] border border-border bg-paper px-3 py-2 text-xs leading-5 text-ink/60">
          证据数据截止：{formatDateTime(evidence.data_cutoff)} · manifest：
          {evidence.manifest_hash
            ? `${evidence.manifest_hash.slice(0, 12)}…`
            : "暂无"}
          。真实邮件、服务商送达和用户确认成交均不会由研究回放推断。
        </p>

        {evidence.unavailable_reason ? (
          <p className="mt-3 rounded-[8px] border border-dashed border-border bg-white px-3 py-3 text-sm text-ink/60">
            正式验证/晋升不足原因：{evidence.unavailable_reason}
          </p>
        ) : null}

        <div className="mt-4 rounded-[10px] border border-border bg-white p-4">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p className="text-sm font-semibold text-ink">积累进度</p>
              <p className="mt-1 text-xs leading-5 text-ink/55">
                Shadow 观察从首个完整 PIT session 开始累计；非买入信号、不会发邮件，也不影响正式榜单、持仓或执行。
              </p>
            </div>
            <span className="w-fit rounded-full bg-paper px-2.5 py-1 text-xs font-semibold text-ink/60">
              {leaderObservationStateLabel(observationState)}
            </span>
          </div>
          <p className="mt-3 text-xs leading-5 text-ink/55">
            观察数据截止：{formatDateTime(observationDataCutoff)} · observation manifest：
            {observationManifestHash
              ? `${observationManifestHash.slice(0, 12)}…`
              : "暂无"}
          </p>
          {evidence.observation_unavailable_reason ? (
            <p className="mt-3 rounded-[8px] border border-dashed border-border bg-paper px-3 py-2 text-xs text-ink/60">
              观察不可用原因：{evidence.observation_unavailable_reason}
            </p>
          ) : null}
          <div className="mt-3 grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
            <EvidenceStat
              label="完整 PIT sessions"
              value={`${materializedPitSessions || eligiblePitSessions}/${requiredPitSessions || "?"}`}
            />
            <EvidenceStat
              label="当日输入 ETF"
              value={`${currentInputAssetCount} 只`}
            />
            <EvidenceStat
              label="可用/符合观察"
              value={`${currentAvailableObservationCount}/${currentQualifyingObservationCount}`}
            />
            <EvidenceStat
              label="主指标独立样本"
              value={`${independentPrimaryDateCount}/${requiredPrimaryDateCount || "?"}`}
            />
            <EvidenceStat
              label="待结算/已成熟"
              value={`${pendingOutcomeCount}/${maturedOutcomeCount}`}
            />
            <EvidenceStat
              label="Walk-forward"
              value={`${completedWalkForwardFoldCount}/${requiredWalkForwardFoldCount || "?"}`}
            />
          </div>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <div className="rounded-[10px] border border-border bg-white p-4">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <p className="text-sm font-semibold text-ink">当日 Shadow 观察</p>
                <p className="mt-1 text-xs leading-5 text-ink/55">
                  最多展示 20 条当前代理观察；仅用于记录条件是否出现，不构成买入、卖出或仓位建议。
                </p>
              </div>
              <span className="w-fit rounded-full bg-paper px-2.5 py-1 text-xs text-ink/60">
                显示 {currentObservations.length}/{currentQualifyingObservationCount}
              </span>
            </div>
            <div className="mt-3 space-y-2">
              {currentObservations.length ? (
                currentObservations.map((observation) => {
                  const reasons = [
                    ...observation.gate_reasons,
                    ...observation.unavailable_reasons
                  ];
                  const componentRows = Object.entries(observation.components).slice(
                    0,
                    3
                  );
                  return (
                    <div
                      key={`${observation.candidate_id}-${observation.asset_code}-${observation.feature_hash}`}
                      className="rounded-[8px] bg-paper px-3 py-2 text-xs leading-5 text-ink/60"
                    >
                      <p className="font-semibold text-ink/75">
                        {observation.candidate_id} · {observation.asset_code}
                      </p>
                      <p>
                        {observation.qualifies
                          ? "当前满足代理条件（研究）"
                          : "当前未形成条件，仅记录观察"}
                        {" · "}
                        {observation.availability === "available"
                          ? "数据可用"
                          : "数据不可用"}
                        {" · 分数 "}
                        {leaderEvidenceValue("score", observation.score)}
                      </p>
                      <p className="text-ink/45">
                        信号日 {formatDateTime(observation.signal_date)} · 截止 {formatDateTime(observation.source_cutoff)}
                        {observation.peer_group
                          ? ` · 同类 ${observation.peer_group}`
                          : ""}
                      </p>
                      {componentRows.length ? (
                        <p className="text-ink/45">
                          组件：
                          {componentRows
                            .map(([key, value]) =>
                              `${key}=${leaderEvidenceValue(key, value)}`
                            )
                            .join(" · ")}
                        </p>
                      ) : null}
                      {reasons.length ? (
                        <p className="text-ink/45">
                          原因：{reasons.slice(0, 5).join("、")}
                        </p>
                      ) : null}
                    </div>
                  );
                })
              ) : (
                <p className="rounded-[8px] bg-paper px-3 py-3 text-xs text-ink/50">
                  当前没有可展示的 Shadow 观察；这不表示收益为零，也不构成现金或交易建议。
                </p>
              )}
            </div>
            {observationHasMore ? (
              <p className="mt-3 text-xs text-ink/45">
                当前符合条件的观察超过页面展示上限，已仅显示前 20 条。
              </p>
            ) : null}
          </div>

          <div className="rounded-[10px] border border-border bg-white p-4">
            <p className="text-sm font-semibold text-ink">待结算结果</p>
            <p className="mt-1 text-xs leading-5 text-ink/55">
              只有相应交易日真实完成后才写入 outcome；待结算不等于零收益，不会提前填充或推断。
            </p>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              {outcomeCounts.length ? (
                outcomeCounts.slice(0, 4).map((outcome) => (
                  <EvidenceStat
                    key={outcome.horizon_sessions}
                    label={`${outcome.horizon_sessions} 日 outcome`}
                    value={`待 ${leaderCount(outcome.pending)} · 成熟 ${leaderCount(outcome.matured)} · 不可用 ${leaderCount(outcome.unavailable)}`}
                  />
                ))
              ) : (
                <p className="rounded-[8px] bg-paper px-3 py-3 text-xs text-ink/50">
                  尚无可结算窗口统计。
                </p>
              )}
            </div>
            <div className="mt-3 space-y-2">
              {pendingOutcomes.length ? (
                pendingOutcomes.map((outcome) => (
                  <div
                    key={`${outcome.candidate_id}-${outcome.asset_code}-${outcome.feature_hash}`}
                    className="rounded-[8px] bg-paper px-3 py-2 text-xs text-ink/60"
                  >
                    <p className="font-semibold text-ink/75">
                      {outcome.candidate_id} · {outcome.asset_code}
                    </p>
                    <p className="mt-1">
                      信号日 {formatDateTime(outcome.signal_date)} · 待结算窗口 {outcome.pending_horizons.join("、")} 日
                    </p>
                  </div>
                ))
              ) : (
                <p className="text-xs text-ink/50">当前没有待结算条目。</p>
              )}
            </div>
          </div>
        </div>

        <div className="mt-5 grid gap-4 xl:grid-cols-2">
          <div className="rounded-[10px] border border-border bg-white p-4">
            <p className="text-sm font-semibold text-ink">来源与非等价说明</p>
            <div className="mt-3 space-y-2 text-xs leading-5 text-ink/60">
              {articles.length ? (
                articles.map((article) => (
                  <p key={String(article.article_id ?? article.source_url)}>
                    <a
                      className="font-semibold text-accent underline decoration-accent/30 underline-offset-2"
                      href={String(article.source_url ?? "#")}
                      rel="noreferrer"
                      target="_blank"
                    >
                      {String(
                        article.title ?? article.article_id ?? "来源文章"
                      )}
                    </a>
                    {article.captured_content_hash
                      ? ` · 内容哈希 ${String(article.captured_content_hash).slice(0, 10)}…`
                      : ""}
                  </p>
                ))
              ) : (
                <p>来源 registry 尚未可用。</p>
              )}
              {statements.slice(0, 6).map((statement) => (
                <div
                  key={String(statement.statement_id)}
                  className="rounded-[8px] bg-paper px-3 py-2"
                >
                  <p className="font-semibold text-ink/70">
                    {String(statement.statement_id)} ·{" "}
                    {String(statement.disclosure_state ?? "未知披露状态")}
                  </p>
                  <p>
                    {String(statement.interpretation ?? "暂无 ETF 代理解释")}
                  </p>
                  <p className="text-ink/45">
                    限制：{String(statement.limitation ?? "未声明")}
                  </p>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-[10px] border border-border bg-white p-4">
            <p className="text-sm font-semibold text-ink">冻结代理公式</p>
            <div className="mt-3 space-y-2">
              {candidates.map((candidate) => (
                <div
                  key={String(candidate.candidate_id)}
                  className="rounded-[8px] bg-paper px-3 py-2 text-xs leading-5 text-ink/60"
                >
                  <p className="font-semibold text-ink/75">
                    {String(candidate.candidate_id)}
                  </p>
                  <p className="mt-1 break-words font-mono text-[11px]">
                    {String(candidate.formula ?? "公式不可用")}
                  </p>
                  <p className="mt-1 text-ink/45">
                    预热 {String(candidate.required_history_sessions ?? "?")} 日
                    · 缺失规则{" "}
                    {String(candidate.missing_value_rule ?? "fail_closed")}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <div className="rounded-[10px] border border-border bg-white p-4">
            <p className="text-sm font-semibold text-ink">
              主指标：Top10 / 5 日配对净超额
            </p>
            <p className="mt-1 text-xs text-ink/50">
              唯一晋升主指标；已扣声明的双边费用与滑点。
            </p>
            <div className="mt-3 space-y-2">
              {evidence.primary_metrics.length ? (
                evidence.primary_metrics.map((metric, index) => (
                  <div
                    key={`${String(metric.candidate_id)}-${index}`}
                    className="rounded-[8px] bg-paper px-3 py-2 text-xs text-ink/60"
                  >
                    <p className="font-semibold text-ink/75">
                      {String(metric.candidate_id ?? "候选")}
                    </p>
                    <p className="mt-1">
                      净超额：
                      {leaderEvidenceValue(
                        "mean_paired_net_excess",
                        metric.mean_paired_net_excess
                      )}{" "}
                      · 独立样本 {String(metric.independent_date_count ?? 0)}
                    </p>
                    <p className="mt-1 text-ink/45">
                      不确定性：
                      {leaderEvidenceValue("inference", metric.inference)}
                    </p>
                  </div>
                ))
              ) : (
                <p className="rounded-[8px] bg-paper px-3 py-3 text-xs text-ink/50">
                  尚无完整主指标，不展示历史赢家。
                </p>
              )}
            </div>
          </div>

          <div className="rounded-[10px] border border-border bg-white p-4">
            <p className="text-sm font-semibold text-ink">
              辅助诊断（不得替代主指标）
            </p>
            <p className="mt-1 text-xs text-ink/50">
              Top5/20、1/3/10 日与 MA5 生命周期均为 exploratory。
            </p>
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
              <EvidenceStat
                label="探索性结果"
                value={`${evidence.exploratory_metrics.length} 项`}
              />
              <EvidenceStat
                label="MA5 policy shadow"
                value={`${evidence.ma5_policy_shadow.length} 项`}
              />
              <EvidenceStat
                label="费用/滑点"
                value={leaderEvidenceValue("costs", evidence.costs)}
              />
              <EvidenceStat
                label="Holdout"
                value={leaderEvidenceValue("holdout", evidence.holdout)}
              />
            </div>
          </div>
        </div>

        <div className="mt-4 grid gap-4 lg:grid-cols-3">
          <div className="rounded-[10px] border border-border bg-white p-4">
            <p className="text-sm font-semibold text-ink">覆盖</p>
            <div className="mt-2 space-y-1 text-xs text-ink/55">
              {coverageRows.length ? (
                coverageRows.map(([key, value]) => (
                  <p key={key}>
                    {key}：{leaderEvidenceValue(key, value)}
                  </p>
                ))
              ) : (
                <p>尚无 PIT 覆盖。</p>
              )}
            </div>
          </div>
          <div className="rounded-[10px] border border-border bg-white p-4">
            <p className="text-sm font-semibold text-ink">排除项</p>
            <div className="mt-2 space-y-1 text-xs text-ink/55">
              {exclusionRows.length ? (
                exclusionRows.map(([key, count]) => (
                  <p key={key}>
                    {key}：{count}
                  </p>
                ))
              ) : (
                <p>未记录排除项。</p>
              )}
            </div>
          </div>
          <div className="rounded-[10px] border border-border bg-white p-4">
            <p className="text-sm font-semibold text-ink">增量与稳定性</p>
            <div className="mt-2 space-y-2 text-xs text-ink/55">
              {diagnostics.map(([label, value]) => (
                <p key={label}>
                  <span className="font-semibold text-ink/70">{label}：</span>
                  {leaderEvidenceValue(label, value)}
                </p>
              ))}
            </div>
          </div>
        </div>

        {evidence.candidate_decisions.length ? (
          <div className="mt-4 rounded-[10px] border border-border bg-white p-4">
            <p className="text-sm font-semibold text-ink">候选门槛结论</p>
            <div className="mt-2 grid gap-2 md:grid-cols-3">
              {evidence.candidate_decisions.map((decision) => (
                <div
                  key={String(decision.candidate_id)}
                  className="rounded-[8px] bg-paper px-3 py-2 text-xs text-ink/60"
                >
                  <p className="font-semibold text-ink/75">
                    {String(decision.candidate_id)}
                  </p>
                  <p className="mt-1">状态：{String(decision.status)}</p>
                  <p className="mt-1 text-ink/45">
                    失败门槛：
                    {leaderEvidenceValue("failed_gates", decision.failed_gates)}
                  </p>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        <div className="mt-4 rounded-[8px] border border-dashed border-border bg-paper px-3 py-3 text-xs leading-5 text-ink/55">
          {evidence.limitations.length
            ? evidence.limitations.join("；")
            : "仅供研究，正式排名和邮件行为保持不变。"}
        </div>
      </section>
    </Panel>
  );
}

type ExitV2BaselineRow = {
  key: string;
  label: string;
  metrics: Record<string, unknown>;
};

function exitV2BaselineRows(data: EtfStrategyComparison | null | undefined) {
  const comparison = asRecord(data?.exit_v2_baseline_comparison);
  const baselines = asRecord(comparison?.baselines);
  if (!baselines) {
    return [];
  }
  const keys: Array<[string, string]> = [
    ["topn_fixed_hold", "TopN 固定持有"],
    ["current_live_exit_rules", "当前退出规则"],
    ["guard_only", "只暂停加仓 Guard"],
    ["exit_v2_reentry", "Exit V2 减仓 + 再入场"]
  ];
  return keys.flatMap(([key, label]): ExitV2BaselineRow[] => {
    const metrics = asRecord(baselines[key]);
    return metrics ? [{ key, label, metrics }] : [];
  });
}

export default function EtfEvidencePage() {
  const queryClient = useQueryClient();

  const backtests = useQuery({
    queryKey: ["short-research", "etf-backtests"],
    queryFn: async () =>
      (
        await api.get<EtfPortfolioBacktestList>(
          "/api/short-research/etf-backtests?limit=1"
        )
      ).data
  });
  const latestBacktestId = backtests.data?.items[0]?.id ?? null;
  const backtestDetail = useQuery({
    queryKey: ["short-research", "etf-backtests", latestBacktestId],
    enabled: latestBacktestId !== null,
    queryFn: async () =>
      (
        await api.get<EtfPortfolioBacktestDetail>(
          `/api/short-research/etf-backtests/${latestBacktestId}`
        )
      ).data
  });
  const strategyComparison = useQuery({
    queryKey: ["short-research", "etf-strategy-comparison", "latest"],
    queryFn: async () =>
      (
        await api.get<EtfStrategyComparison | null>(
          "/api/short-research/etf-strategy-comparisons/latest"
        )
      ).data
  });
  const strategyHealthcheck = useQuery({
    queryKey: ["short-research", "etf-strategy-healthcheck", "latest"],
    queryFn: async () =>
      (
        await api.get<EtfStrategyHealthcheck | null>(
          "/api/short-research/etf-strategy-healthcheck/latest"
        )
      ).data
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
      (
        await api.get<EtfOptimizedAllocation | null>(
          "/api/short-research/etf-optimized-allocation/latest"
        )
      ).data
  });
  const exitHyperopt = useQuery({
    queryKey: ["short-research", "etf-exit-hyperopt", "latest"],
    queryFn: async () =>
      (
        await api.get<EtfExitHyperopt | null>(
          "/api/short-research/etf-exit-hyperopt/latest"
        )
      ).data
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
  const evidenceOverview = useQuery({
    queryKey: ["short-research", "evidence", "etf", "latest"],
    queryFn: async () =>
      (
        await api.get<EtfEvidenceOverview>(
          "/api/short-research/evidence/etf/latest"
        )
      ).data
  });
  const leaderTacticsEvidence = useQuery({
    queryKey: ["short-research", "evidence", "etf", "leader-tactics", "latest"],
    queryFn: async () =>
      (
        await api.get<EtfLeaderTacticsEvidence>(
          "/api/short-research/evidence/etf/leader-tactics/latest"
        )
      ).data
  });

  const runBacktest = useMutation({
    mutationFn: async (executionModel: "daily_close" | "intraday_alert") =>
      (
        await api.post<EtfPortfolioBacktestDetail>(
          "/api/short-research/etf-backtests",
          {
            days: 730,
            fee_rate: 0.001,
            max_assets: 500,
            execution_model: executionModel
          }
        )
      ).data,
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["short-research", "etf-backtests"]
      });
    }
  });
  const runComparison = useMutation({
    mutationFn: async () =>
      (
        await api.post<EtfStrategyComparison>(
          "/api/short-research/etf-strategy-comparisons",
          {
            days: 730,
            fee_rate: 0.001,
            max_assets: 500
          }
        )
      ).data,
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["short-research", "etf-strategy-comparison"]
      });
    }
  });
  const runHealthcheck = useMutation({
    mutationFn: async () =>
      (
        await api.post<EtfStrategyHealthcheck>(
          "/api/short-research/etf-strategy-healthcheck/run"
        )
      ).data,
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["short-research", "etf-strategy-healthcheck"]
      });
    }
  });
  const runOptimizedAllocation = useMutation({
    mutationFn: async () =>
      (
        await api.post<EtfOptimizedAllocation>(
          "/api/short-research/etf-optimized-allocation/run"
        )
      ).data,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["short-research", "etf-optimized-allocation"]
        }),
        queryClient.invalidateQueries({
          queryKey: ["short-research", "observation-portfolio"]
        })
      ]);
    }
  });
  const runExitHyperopt = useMutation({
    mutationFn: async () =>
      (await api.post<JobRun>("/api/admin/jobs/etf_exit_hyperopt/run?days=730"))
        .data,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["short-research", "etf-exit-hyperopt"]
        }),
        queryClient.invalidateQueries({ queryKey: ["admin", "jobs"] })
      ]);
    }
  });
  const runExitCredibility = useMutation({
    mutationFn: async () =>
      (
        await api.post<JobRun>(
          "/api/admin/jobs/etf_exit_signal_credibility/run?days=730&max_assets=50&universe_scope=latest_opportunity_top"
        )
      ).data,
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: ["short-research", "etf-exit-credibility"]
        }),
        queryClient.invalidateQueries({ queryKey: ["admin", "jobs"] })
      ]);
    }
  });

  const detail = backtestDetail.data;
  const isIntradayBacktest =
    backtestExecutionModel(detail) === "intraday_alert_v1";
  const coverageEvidence = detail?.coverage_evidence;
  const timeLimitations = detail?.time_resolution_limitations;
  const optimized =
    observationPortfolio.data?.optimized_allocation ??
    optimizedAllocation.data ??
    null;
  const labelEvidenceRows = useMemo(
    () => buildLabelEvidenceRows(detail?.label_summaries ?? []),
    [detail?.label_summaries]
  );
  const exitV2Rows = useMemo(
    () => exitV2BaselineRows(strategyComparison.data),
    [strategyComparison.data]
  );
  const exitV2Comparison = asRecord(
    strategyComparison.data?.exit_v2_baseline_comparison
  );
  const exitV2Conclusion =
    metadataString(exitV2Comparison ?? {}, "conclusion") ?? "等待 V2 对照";
  const healthcheckIssue = healthcheckEvidenceIssue(strategyHealthcheck.data);
  const healthcheckItems = visibleHealthcheckItems(strategyHealthcheck.data);
  const exitCredibilityGroups = useMemo(
    () => groupedCredibilityItems(exitCredibility.data?.items ?? []),
    [exitCredibility.data?.items]
  );
  const hyperoptCoverageRaw =
    exitHyperopt.data?.summary.coverage ??
    exitHyperopt.data?.summary.coverage_funnel;
  const hyperoptCoverage =
    typeof hyperoptCoverageRaw === "object" && hyperoptCoverageRaw !== null
      ? (hyperoptCoverageRaw as Record<string, unknown>)
      : {};

  return (
    <div className="space-y-5">
      <Panel>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">
              策略证据
            </p>
            <h2 className="mt-1 text-2xl font-semibold tracking-[-0.02em] text-ink">
              ETF 工作台策略证据
            </h2>
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
              重新生成策略体检
            </button>
            <button
              className="rounded-[6px] bg-ink px-3 py-2 text-sm font-semibold text-white transition hover:bg-ink/85 disabled:opacity-60"
              disabled={runExitHyperopt.isPending}
              onClick={() => runExitHyperopt.mutate()}
            >
              生成止盈止损优化证据
            </button>
            <button
              className="rounded-[6px] bg-ink px-3 py-2 text-sm font-semibold text-white transition hover:bg-ink/85 disabled:opacity-60"
              disabled={runExitCredibility.isPending}
              onClick={() => runExitCredibility.mutate()}
            >
              验证止盈止损规则
            </button>
          </div>
        </div>
        {[
          runBacktest,
          runComparison,
          runHealthcheck,
          runOptimizedAllocation,
          runExitHyperopt,
          runExitCredibility
        ].map((mutation, index) =>
          mutation.isError ? (
            <p
              key={index}
              className="mt-3 rounded-[8px] border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700"
            >
              {errorText(mutation.error)}
            </p>
          ) : null
        )}
      </Panel>

      <Panel>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-sm font-semibold text-ink">PIT 证据闭环</p>
            <p className="mt-1 text-xs leading-5 text-ink/55">
              正式策略、研究回放、policy
              shadow、SMTP、服务商送达和真实成交严格分层；模拟结果不会升级为生产业绩。
            </p>
          </div>
          <span className="w-fit rounded-full bg-paper px-2.5 py-1 text-xs text-ink/60">
            {evidenceOverview.data
              ? `更新 ${formatDateTime(evidenceOverview.data.generated_at)}`
              : evidenceOverview.isLoading
                ? "加载证据"
                : "证据接口不可用"}
          </span>
        </div>
        {evidenceOverview.data ? (
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {ETF_EVIDENCE_SURFACES.map(([key, title]) => (
              <EtfEvidenceSurfaceCard
                key={key}
                title={title}
                surface={evidenceOverview.data.surfaces[key]}
              />
            ))}
          </div>
        ) : (
          <p className="mt-4 rounded-[8px] border border-dashed border-border bg-paper px-3 py-4 text-sm text-ink/55">
            暂无可验证的 PIT 证据；不会回退到旧榜单、原始价或模拟邮件结果。
          </p>
        )}
      </Panel>

      {leaderTacticsEvidence.data ? (
        <LeaderTacticsEvidencePanel evidence={leaderTacticsEvidence.data} />
      ) : (
        <Panel>
          <section
            data-evidence-family="leader-tactics-shadow"
            data-research-only="true"
          >
            <p className="text-sm font-semibold text-ink">
              龙头战术透明代理（研究）
            </p>
            <p className="mt-3 rounded-[8px] border border-dashed border-border bg-paper px-3 py-4 text-sm text-ink/55">
              {leaderTacticsEvidence.isLoading
                ? "正在读取独立研究证据。"
                : "龙头代理证据接口不可用；不会回退到当前榜单、原始价或模拟实盘结果。"}
            </p>
          </section>
        </Panel>
      )}

      <Panel>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-sm font-semibold text-ink">止盈止损规则验证</p>
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
              <EvidenceStat
                label="覆盖 ETF"
                value={metricInteger(
                  exitCredibility.data.summary,
                  "asset_count"
                )}
              />
              <EvidenceStat
                label="理论事件"
                value={metricInteger(
                  exitCredibility.data.summary,
                  "event_count"
                )}
              />
              <EvidenceStat
                label="已验证信号"
                value={metricInteger(
                  exitCredibility.data.summary,
                  "verified_signal_count"
                )}
              />
              <EvidenceStat
                label="数据截止"
                value={formatDateTime(exitCredibility.data.data_cutoff)}
              />
            </div>
            {exitCredibility.data.insufficiency_reasons.length ? (
              <div className="mt-4 rounded-[8px] border border-dashed border-border bg-paper px-3 py-3 text-sm text-ink/60">
                {exitCredibility.data.insufficiency_reasons.join("；")}
              </div>
            ) : null}
            <div className="mt-4 space-y-4">
              {exitCredibilityGroups.map((group) => (
                <div
                  key={group.policyClass}
                  className="rounded-[8px] border border-border bg-paper/40 p-3"
                >
                  <div className="flex flex-col gap-1 sm:flex-row sm:items-start sm:justify-between">
                    <div>
                      <p className="text-sm font-semibold text-ink">
                        {group.label}
                      </p>
                      <p className="mt-1 text-xs text-ink/55">
                        {policyGroupDescription(group.policyClass)}
                      </p>
                    </div>
                    <span className="w-fit rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-ink/55">
                      {group.items.length} 类信号
                    </span>
                  </div>
                  <div className="mt-3 grid gap-3 lg:grid-cols-2">
                    {group.items.map((item) => (
                      <div
                        key={item.id}
                        className="rounded-[8px] border border-border bg-white p-3"
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <p className="text-sm font-semibold text-ink">
                              {signalLabel(item.signal_type)}
                            </p>
                            <p className="mt-1 text-xs text-ink/55">
                              {item.evidence_level} · 样本 {item.sample_count} ·{" "}
                              {policyStatusLabel(item.evidence_status)}
                            </p>
                          </div>
                          <span className="rounded-full bg-paper px-2.5 py-1 text-[11px] font-semibold text-ink/60">
                            {item.strong_conclusion_allowed
                              ? "可参考"
                              : "弱证据"}
                          </span>
                        </div>
                        <p className="mt-2 text-xs text-ink/55">
                          {typeof item.kpi_summary?.focus === "string"
                            ? item.kpi_summary.focus
                            : (item.recommended_usage ?? "仅供研究参考。")}
                        </p>
                        <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-ink/60">
                          {kpiMetrics(item.kpi_summary).map((metric) => (
                            <span key={metric.key}>
                              {metric.label}：{kpiText(metric)}
                            </span>
                          ))}
                        </div>
                        {item.events.length ? (
                          <div className="mt-3 space-y-1 border-t border-border pt-3 text-xs text-ink/50">
                            {item.events.slice(0, 2).map((event) => (
                              <p key={event.id}>
                                {event.etf_name ?? event.etf_code} ·{" "}
                                {formatDate(event.signal_date)} ·{" "}
                                {event.outcome}
                              </p>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </>
        ) : (
          <p className="mt-4 rounded-[8px] border border-dashed border-border bg-paper px-3 py-4 text-sm text-ink/55">
            暂无止盈止损规则验证。可以点击“验证止盈止损规则”运行一次，或等待服务器夜间任务。
          </p>
        )}
      </Panel>

      <div className="grid gap-5 xl:grid-cols-[1.35fr_0.9fr]">
        <Panel>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
            <div>
              <p className="text-sm font-semibold text-ink">回测证据</p>
              <p className="mt-1 text-xs text-ink/55">
                {backtestExecutionLabel(detail)}
              </p>
            </div>
            <div className="flex flex-wrap gap-2 text-xs text-ink/55">
              <span className="rounded-full bg-paper px-2.5 py-1">
                {detail?.evidence_status ?? "等待验证"}
              </span>
              <span className="rounded-full bg-paper px-2.5 py-1">
                {detail
                  ? `${formatDate(detail.start_date)} - ${formatDate(detail.end_date)}`
                  : "暂无区间"}
              </span>
            </div>
          </div>
          <p className="mt-3 rounded-[8px] border border-border bg-paper px-3 py-2 text-xs leading-5 text-ink/60">
            {evidenceContractText(
              detail?.evidence_status,
              detail?.evidence_summary
            )}
          </p>
          <div className="mt-4 grid gap-3 md:grid-cols-2 xl:grid-cols-5">
            <EvidenceDimension
              label="动作"
              value={
                detail?.action_evidence.scenario_label ?? "动作建议完全执行情景"
              }
              detail={backtestEvidenceSampleText(
                detail?.action_evidence.status,
                detail?.action_evidence.sample_count
              )}
            />
            <EvidenceDimension
              label="通知"
              value={
                detail?.notification_evidence.scenario_label ??
                "仅 SMTP 已接受邮件被执行敏感性"
              }
              detail={`${backtestEvidenceSampleText(
                detail?.notification_evidence.status,
                detail?.notification_evidence.sample_count
              )}；SMTP 接受不等于送达或执行`}
            />
            <EvidenceDimension
              label="执行模型"
              value={detail?.execution_evidence.label ?? "等待回测"}
              detail={backtestFillFieldLabel(
                detail?.execution_evidence.base_fill_field
              )}
            />
            <EvidenceDimension
              label="数据覆盖"
              value={
                coverageEvidence
                  ? `${coverageEvidence.priced_asset_count ?? 0} / ${coverageEvidence.asset_count ?? 0} 只 ETF`
                  : "暂无"
              }
              detail={
                coverageEvidence
                  ? `${coverageEvidence.trading_days ?? 0} 个交易日${
                      coverageEvidence.intraday_quote_count
                        ? ` · ${coverageEvidence.intraday_quote_count} 条盘中快照`
                        : ""
                    }`
                  : "等待覆盖统计"
              }
            />
            <EvidenceDimension
              label="时间粒度限制"
              value={
                timeLimitations?.resolution === "stored_intraday_snapshots"
                  ? "已存盘中快照"
                  : "仅日线"
              }
              detail={
                timeLimitations?.notes?.[0] ??
                "盘中触发、bid/ask、IOPV、SMTP 送达和用户真实执行均未验证。"
              }
            />
          </div>
          <div className="mt-4 grid gap-3 md:grid-cols-4">
            <EvidenceStat
              label="历史收益"
              value={metricPercent(detail?.metrics, "cumulative_return")}
            />
            <EvidenceStat
              label="最大回撤"
              value={metricPercent(detail?.metrics, "max_drawdown")}
            />
            <EvidenceStat
              label="胜率"
              value={metricPercent(detail?.metrics, "win_rate")}
            />
            <EvidenceStat
              label="交易次数"
              value={metricInteger(detail?.metrics, "trade_count")}
            />
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
                  <XAxis
                    dataKey="label"
                    tickLine={false}
                    axisLine={false}
                    minTickGap={28}
                  />
                  <YAxis tickLine={false} axisLine={false} width={64} />
                  <Tooltip
                    formatter={(value) => formatCurrency(Number(value))}
                  />
                  <Line
                    type="monotone"
                    dataKey="equity"
                    name="策略权益"
                    stroke="#111"
                    dot={false}
                    strokeWidth={2}
                  />
                  <Line
                    type="monotone"
                    dataKey="benchmark"
                    name="宽基对照"
                    stroke="#777"
                    dot={false}
                    strokeWidth={2}
                  />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-ink/45">
                暂无回测曲线。
              </div>
            )}
          </div>
        </Panel>

        <Panel>
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-sm font-semibold text-ink">标签组合有效性</p>
              <p className="mt-1 text-xs text-ink/55">
                按买入观察和今日买点分组，展示历史前瞻表现。
              </p>
            </div>
            <Link
              href="/short-term"
              className="rounded-[6px] border border-border px-3 py-2 text-xs font-semibold text-ink/70"
            >
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
                    <tr
                      key={`${row.label}-${row.entryTimingLabel}`}
                      className="border-t border-border align-top"
                    >
                      <td className="px-3 py-2 font-semibold text-ink">
                        {row.label} + {row.entryTimingLabel}
                      </td>
                      <td className="px-3 py-2 text-ink/65">
                        {formatEvidencePercent(row.fiveDay?.medianReturn)}
                      </td>
                      <td className="px-3 py-2 text-ink/65">
                        {formatEvidencePercent(row.fiveDay?.winRate)}
                      </td>
                      <td className="px-3 py-2 text-ink/65">
                        {formatEvidencePercent(row.tenDay?.medianReturn)}
                      </td>
                      <td className="px-3 py-2 text-ink/65">
                        {formatEvidencePercent(row.tenDay?.winRate)}
                      </td>
                      <td className="px-3 py-2 text-ink/65">
                        5日 {row.fiveDay?.sampleCount ?? 0} / 10日{" "}
                        {row.tenDay?.sampleCount ?? 0}
                      </td>
                      <td className="px-3 py-2 text-ink/75">
                        {labelEvidenceConclusion(row)}
                      </td>
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
            {exitV2Rows.length ? (
              <div className="overflow-x-auto rounded-[10px] border border-border">
                <div className="flex flex-col gap-1 border-b border-border bg-paper px-3 py-2 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-xs font-semibold text-ink">
                    Exit V2 对照基准
                  </p>
                  <p className="text-xs text-ink/55">{exitV2Conclusion}</p>
                </div>
                <table className="min-w-[820px] w-full border-collapse text-left text-xs">
                  <thead className="bg-paper text-ink/55">
                    <tr>
                      <th className="px-3 py-2 font-medium">策略基线</th>
                      <th className="px-3 py-2 font-medium">累计收益</th>
                      <th className="px-3 py-2 font-medium">最大回撤</th>
                      <th className="px-3 py-2 font-medium">卖飞率</th>
                      <th className="px-3 py-2 font-medium">保护率</th>
                      <th className="px-3 py-2 font-medium">错误退出</th>
                      <th className="px-3 py-2 font-medium">再入场</th>
                      <th className="px-3 py-2 font-medium">提醒次数</th>
                    </tr>
                  </thead>
                  <tbody>
                    {exitV2Rows.map((row) => (
                      <tr
                        key={row.key}
                        className="border-t border-border align-top"
                      >
                        <td className="px-3 py-2 font-semibold text-ink">
                          {row.label}
                        </td>
                        <td className="px-3 py-2 text-ink/65">
                          {metricPercent(row.metrics, "cumulative_return")}
                        </td>
                        <td className="px-3 py-2 text-ink/65">
                          {metricPercent(row.metrics, "max_drawdown")}
                        </td>
                        <td className="px-3 py-2 text-ink/65">
                          {metricPercent(row.metrics, "missed_upside_rate")}
                        </td>
                        <td className="px-3 py-2 text-ink/65">
                          {metricPercent(
                            row.metrics,
                            "protection_success_rate"
                          )}
                        </td>
                        <td className="px-3 py-2 text-ink/65">
                          {metricInteger(row.metrics, "false_exit_count")}
                        </td>
                        <td className="px-3 py-2 text-ink/65">
                          {metricInteger(row.metrics, "reentry_count")}
                        </td>
                        <td className="px-3 py-2 text-ink/65">
                          {metricInteger(row.metrics, "alert_count")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            {strategyComparison.data?.strategies.map((strategy) => (
              <div
                key={strategy.strategy_key}
                className="rounded-[8px] border border-border bg-paper/40 p-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm font-semibold text-ink">
                    {strategy.strategy_label}
                  </p>
                  <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-ink/55">
                    {strategy.strategy_key}
                  </span>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-ink/60">
                  <span>
                    累计收益：
                    {metricPercent(strategy.metrics, "cumulative_return")}
                  </span>
                  <span>
                    最大回撤：{metricPercent(strategy.metrics, "max_drawdown")}
                  </span>
                  <span>
                    波动率：{metricPercent(strategy.metrics, "volatility")}
                  </span>
                  <span>
                    交易次数：{metricInteger(strategy.metrics, "trade_count")}
                  </span>
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
              <p className="mt-1 text-xs text-ink/55">
                体检日：{formatDate(strategyHealthcheck.data?.as_of_date)}
              </p>
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
              {healthcheckIssue ? (
                <div className="mt-3 rounded-[8px] border border-dashed border-border bg-paper/60 p-3 text-sm leading-6 text-ink/65">
                  <p className="font-semibold text-ink">
                    当前体检不能作为当前策略证据
                  </p>
                  <p className="mt-1">{healthcheckIssue}</p>
                  <p className="mt-1 text-xs text-ink/50">
                    建议先运行日线/盘中回测和标签历史回放，再重新生成策略体检。
                  </p>
                </div>
              ) : (
                <div className="mt-3 grid gap-2 sm:grid-cols-2">
                  {healthcheckItems.slice(0, 6).map((item) => (
                    <div
                      key={`${item.item_type}-${item.item_key}`}
                      className="rounded-[8px] border border-border bg-paper/40 p-3 text-xs leading-5 text-ink/60"
                    >
                      <p className="text-sm font-semibold text-ink">
                        {item.item_key}
                      </p>
                      <p>结论：{item.conclusion}</p>
                      <p>样本：{item.sample_count}</p>
                      <p>
                        胜率：
                        {item.win_rate === null
                          ? "暂无"
                          : formatPercent(item.win_rate * 100)}
                      </p>
                    </div>
                  ))}
                </div>
              )}
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
              研究证据，不自动改变提醒；候选参数必须人工确认后才可能进入生效规则。
            </p>
          </div>
          <span className="w-fit rounded-full bg-paper px-2.5 py-1 text-xs text-ink/60">
            {exitHyperopt.data
              ? `${formatDate(exitHyperopt.data.as_of_date)} · ${exitHyperopt.data.objective}`
              : "暂无结果"}
          </span>
        </div>
        {exitHyperopt.data ? (
          <>
            <div className="mt-4 grid gap-3 md:grid-cols-4">
              <EvidenceStat
                label="分桶数量"
                value={metricInteger(exitHyperopt.data.summary, "bucket_count")}
              />
              <EvidenceStat
                label="候选数量"
                value={metricInteger(
                  exitHyperopt.data.summary,
                  "candidate_count"
                )}
              />
              <EvidenceStat
                label="证据不足"
                value={metricInteger(
                  exitHyperopt.data.summary,
                  "evidence_insufficient_count"
                )}
              />
              <EvidenceStat
                label="数据截止"
                value={formatDateTime(exitHyperopt.data.data_cutoff)}
              />
            </div>
            <div className="mt-3 grid gap-3 md:grid-cols-3">
              <EvidenceStat
                label="执行模型"
                value={exitHyperopt.data.execution_model ?? "日线收盘"}
              />
              <EvidenceStat
                label="覆盖口径"
                value={
                  exitHyperopt.data.summary.sampled === true
                    ? "抽样结果"
                    : "全量候选"
                }
              />
              <EvidenceStat
                label="手动延迟"
                value={`${metricInteger(exitHyperopt.data.summary, "manual_delay_minutes")} 分钟`}
              />
            </div>
            <div className="mt-3 grid gap-3 md:grid-cols-4">
              <EvidenceStat
                label="全市场 ETF"
                value={metricInteger(hyperoptCoverage, "all_etf_count")}
              />
              <EvidenceStat
                label="可优化候选"
                value={metricInteger(hyperoptCoverage, "eligible_count")}
              />
              <EvidenceStat
                label="盘中样本足够"
                value={metricInteger(
                  hyperoptCoverage,
                  "enough_intraday_history_count"
                )}
              />
              <EvidenceStat
                label="完成优化"
                value={metricInteger(hyperoptCoverage, "final_optimized_count")}
              />
            </div>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <EvidenceStat
                label="校准版本"
                value={exitHyperopt.data.calibration_rule_version ?? "旧口径"}
              />
              <EvidenceStat
                label="证据版本"
                value={
                  metadataString(
                    exitHyperopt.data.summary,
                    "policy_validation_version"
                  ) ?? "旧口径"
                }
              />
              <EvidenceStat
                label="保护层版本"
                value={
                  metadataString(
                    exitHyperopt.data.summary,
                    "protection_guard_version"
                  ) ?? "暂无"
                }
              />
              <EvidenceStat
                label="生效状态"
                value={
                  exitHyperopt.data.summary.approved_for_live === true
                    ? "已批准生效"
                    : "研究候选，未生效"
                }
              />
            </div>
            <div className="mt-3 grid gap-3 md:grid-cols-2">
              <EvidenceStat
                label="合同 hash"
                value={
                  exitHyperopt.data.contract_hash
                    ? exitHyperopt.data.contract_hash.slice(0, 10)
                    : "暂无"
                }
              />
              <EvidenceStat
                label="样本口径"
                value={
                  metadataString(exitHyperopt.data.summary, "universe_scope") ??
                  "旧口径"
                }
              />
            </div>
            <div className="mt-4 grid gap-3 lg:grid-cols-2">
              {exitHyperopt.data.items.slice(0, 6).map((item) => (
                <div
                  key={item.id}
                  className="rounded-[8px] border border-border bg-paper/40 p-3"
                >
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
                    <span>
                      样本外收益：
                      {metricPercent(
                        item.out_of_sample_metrics,
                        "total_return"
                      )}
                    </span>
                    <span>
                      样本外回撤：
                      {metricPercent(
                        item.out_of_sample_metrics,
                        "max_drawdown"
                      )}
                    </span>
                    <span>
                      胜率：
                      {metricPercent(item.out_of_sample_metrics, "win_rate")}
                    </span>
                    <span>
                      提醒次数：
                      {metricInteger(item.out_of_sample_metrics, "alert_count")}
                    </span>
                    <span>交易次数：{item.trade_count}</span>
                    <span>样本：{item.sample_count}</span>
                    <span>
                      滚动稳定率：
                      {metricPercent(
                        item.rolling_metrics,
                        "stable_window_rate"
                      )}
                    </span>
                    <span>
                      最差窗口收益：
                      {metricPercent(
                        item.rolling_metrics,
                        "worst_window_return"
                      )}
                    </span>
                    <span>
                      错杀率：
                      {metricPercent(
                        item.out_of_sample_metrics,
                        "missed_upside_rate"
                      )}
                    </span>
                    <span>
                      保护率：
                      {metricPercent(
                        item.out_of_sample_metrics,
                        "protected_exit_rate"
                      )}
                    </span>
                    <span>
                      对默认规则：
                      {item.baseline_comparison.beats_baseline === true
                        ? "更好"
                        : "未胜出"}
                    </span>
                    <span>覆盖：{item.coverage_status ?? "旧口径"}</span>
                    <span>
                      未成交：
                      {metricInteger(
                        item.out_of_sample_metrics,
                        "unfilled_count"
                      )}
                    </span>
                    <span>
                      延迟：{item.manual_delay_minutes ?? "暂无"} 分钟
                    </span>
                    <span>
                      证据等级：
                      {policyLevelLabel(
                        metadataString(item.confidence, "policy_evidence_level")
                      )}
                    </span>
                    <span>
                      策略类型：
                      {item.policy_class_label ?? item.policy_class ?? "旧口径"}
                    </span>
                    <span>
                      证据状态：{policyStatusLabel(item.evidence_status)}
                    </span>
                    <span>
                      批准状态：
                      {item.approved_for_live
                        ? "已批准生效"
                        : (item.approval_status ?? "研究候选")}
                    </span>
                    <span>
                      保护层：{item.protection_guard_version ?? "暂无"}
                    </span>
                    <span>
                      Guard 压制：
                      {metricInteger(
                        item.guard_enabled_metrics,
                        "guard_suppressed_alert_count"
                      )}
                    </span>
                    <span>数据源：{item.source_reliability ?? "旧口径"}</span>
                  </div>
                  <p className="mt-2 rounded-[6px] bg-white px-2 py-1 text-xs text-ink/55">
                    {item.recommended_usage ??
                      "候选参数只作为研究证据，不自动改变邮件规则。"}
                  </p>
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
            暂无参数优化证据。可以点击“生成止盈止损优化证据”运行一次，或等待服务器夜间任务。
          </p>
        )}
      </Panel>

      <Panel>
        <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
          <div>
            <p className="text-sm font-semibold text-ink">组合配置证据</p>
            <p className="mt-1 text-xs text-ink/55">
              模式：{observationPortfolio.data?.portfolio_mode ?? "暂无"} ·
              权重合计{" "}
              {observationPortfolio.data
                ? formatPercent(
                    (observationPortfolio.data.weight_sum ?? 0) * 100
                  )
                : "暂无"}
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
              <div
                key={method.method}
                className="rounded-[8px] border border-border bg-paper/40 p-3"
              >
                <div className="flex items-start justify-between gap-3">
                  <p className="text-sm font-semibold text-ink">
                    {method.label}
                  </p>
                  <span className="rounded-full bg-white px-2.5 py-1 text-[11px] font-semibold text-ink/55">
                    {formatPercent(method.weight_sum * 100)}
                  </span>
                </div>
                <div className="mt-3 space-y-2">
                  {method.items.slice(0, 5).map((item) => (
                    <div
                      key={item.code}
                      className="flex items-center justify-between gap-3 text-xs text-ink/60"
                    >
                      <span className="truncate">{item.name}</span>
                      <span className="font-semibold text-ink">
                        {formatPercent(item.target_weight * 100)}
                      </span>
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
              const executionTime = metadataString(
                trade.metadata,
                "execution_time"
              );
              const signalPrice = metadataNumber(
                trade.metadata,
                "signal_price"
              );
              const executionPrice = metadataNumber(
                trade.metadata,
                "execution_price"
              );
              const delay = metadataNumber(
                trade.metadata,
                "execution_delay_minutes"
              );
              return (
                <div
                  key={trade.id}
                  className="rounded-[8px] bg-paper px-3 py-2"
                >
                  <p>
                    {formatDate(trade.trade_date)} ·{" "}
                    {trade.side === "buy" ? "买入" : "卖出"} {trade.etf_name}{" "}
                    {formatCurrency(trade.amount)} · {trade.reason}
                  </p>
                  {isIntradayBacktest && signalTime ? (
                    <p className="mt-1 text-ink/45">
                      提醒 {formatDateTime(signalTime)}
                      {signalPrice === null
                        ? ""
                        : ` @ ${signalPrice.toFixed(4)}`}
                      {" → "}
                      成交 {formatDateTime(executionTime)}
                      {executionPrice === null
                        ? ""
                        : ` @ ${executionPrice.toFixed(4)}`}
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

function EvidenceDimension({
  label,
  value,
  detail
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="rounded-[10px] border border-border bg-paper/45 px-3 py-3">
      <p className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink/45">
        {label}
      </p>
      <p className="mt-1 text-sm font-semibold leading-5 text-ink">{value}</p>
      <p className="mt-1 text-xs leading-5 text-ink/55">{detail}</p>
    </div>
  );
}
