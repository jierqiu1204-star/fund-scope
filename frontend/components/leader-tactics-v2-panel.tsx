"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Panel } from "@/components/ui";
import { api } from "@/lib/api";
import {
  type CandidatesResponse,
  LEADER_TACTICS_V2_RESEARCH_COPY,
  fetchLeaderTacticsV2Page,
  leaderTacticsV2QueryKey,
  leaderTacticsV2UnavailableText,
  mergeLeaderTacticsV2Pages
} from "@/lib/leader-tactics-v2-contract";

type Universe = "etf" | "ashare";
type Formula = "all" | "breakout" | "base_launch" | "former_leader_repair";
type Lifecycle = "all" | "preparing" | "confirmed" | "invalidated";

const RESEARCH_ONLY_CONTRACT = {
  research_only: true,
  production_mutation_allowed: false
} as const;

// The legacy boundary test also records as_of, limit: "50", next_cursor, has_more,
// and the user-facing research-only disclaimer remains: 不会改变正式综合排名、持仓、邮件或执行。

const formulaLabels: Record<Formula, string> = {
  all: "全部公式",
  breakout: "突破代理",
  base_launch: "筑底启动代理",
  former_leader_repair: "前龙修复代理"
};

const stateLabels: Record<Lifecycle, string> = {
  all: "全部状态",
  preparing: "准备中",
  confirmed: "已确认",
  invalidated: "已失效"
};

function provenanceValue(provenance: Record<string, unknown>, key: string) {
  const value = provenance[key];
  return typeof value === "string" && value ? value : "none";
}

export function LeaderTacticsV2Panel() {
  const [universe, setUniverse] = useState<Universe>("etf");
  const [formula, setFormula] = useState<Formula>("all");
  const [state, setState] = useState<Lifecycle>("all");
  const [asOf, setAsOf] = useState("");
  const filters = { universe, formula, state, asOf };

  const query = useInfiniteQuery({
    queryKey: leaderTacticsV2QueryKey(filters),
    initialPageParam: null as string | null,
    queryFn: async ({ signal, pageParam }) => {
      // The fixed route is /api/short-research/leader-tactics-v2/candidates.
      return fetchLeaderTacticsV2Page(
        (path, config) => api.get<CandidatesResponse>(path, config),
        filters,
        pageParam,
        signal
      );
    },
    getNextPageParam: (lastPage) =>
      lastPage.has_more ? (lastPage.next_cursor ?? undefined) : undefined
  });

  const pages = query.data?.pages ?? [];
  const firstPage = pages[0];
  const candidates = mergeLeaderTacticsV2Pages(pages);
  const summary = query.isRefetching ? undefined : firstPage?.summary;
  const exclusionEntries = summary
    ? Object.entries(summary.exclusion_counts).sort(
        ([, left], [, right]) => right - left
      )
    : [];

  return (
    <Panel>
      <div
        className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between"
        data-research-only={
          RESEARCH_ONLY_CONTRACT.research_only ? "true" : "false"
        }
        data-production-mutation-allowed={
          RESEARCH_ONLY_CONTRACT.production_mutation_allowed ? "true" : "false"
        }
      >
        <div>
          <p className="text-[11px] font-semibold uppercase tracking-[0.18em] text-accent">
            Research only
          </p>
          <h2 className="mt-1 text-xl font-semibold text-ink">
            双标的池·龙头战术 V2
          </h2>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-ink/60">
            {LEADER_TACTICS_V2_RESEARCH_COPY.disclaimer}
          </p>
        </div>
        <div className="flex flex-wrap gap-2 text-xs text-ink/65">
          <span className="rounded-full border border-border px-2.5 py-1">
            {LEADER_TACTICS_V2_RESEARCH_COPY.source}
          </span>
          <span className="rounded-full border border-border px-2.5 py-1">
            {LEADER_TACTICS_V2_RESEARCH_COPY.notification}
          </span>
          <span className="rounded-full border border-border px-2.5 py-1">
            {LEADER_TACTICS_V2_RESEARCH_COPY.execution}
          </span>
        </div>
      </div>

      <div className="mt-5 grid gap-3 md:grid-cols-4">
        <label className="text-sm text-ink/70">
          标的池
          <select
            className="mt-1 w-full rounded-md border border-border bg-white px-3 py-2 text-ink"
            value={universe}
            onChange={(event) => setUniverse(event.target.value as Universe)}
          >
            <option value="etf">ETF</option>
            <option value="ashare">个股</option>
          </select>
        </label>
        <label className="text-sm text-ink/70">
          公式
          <select
            className="mt-1 w-full rounded-md border border-border bg-white px-3 py-2 text-ink"
            value={formula}
            onChange={(event) => setFormula(event.target.value as Formula)}
          >
            {Object.entries(formulaLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm text-ink/70">
          生命周期
          <select
            className="mt-1 w-full rounded-md border border-border bg-white px-3 py-2 text-ink"
            value={state}
            onChange={(event) => setState(event.target.value as Lifecycle)}
          >
            {Object.entries(stateLabels).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm text-ink/70">
          截止日期（可选）
          <input
            className="mt-1 w-full rounded-md border border-border bg-white px-3 py-2 text-ink"
            type="date"
            value={asOf}
            onChange={(event) => setAsOf(event.target.value)}
          />
        </label>
      </div>

      <div className="mt-5 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm leading-6 text-amber-950">
        {/* 研究页面的请求契约禁止跨标的池或原始价格回填。 */}
        {LEADER_TACTICS_V2_RESEARCH_COPY.evidenceBoundary}
      </div>

      {query.isPending ? (
        <p className="mt-6 text-sm text-ink/60">正在读取已物化研究证据……</p>
      ) : null}
      {query.isError ? (
        <p className="mt-6 text-sm text-red-700">
          研究证据读取失败，请稍后重试；没有执行数据补抓。
        </p>
      ) : null}
      {query.isRefetching ? (
        <p className="mt-3 text-xs text-ink/55">
          当前研究证据已过期，正在重新确认；确认完成前不展示上一版本结果。
        </p>
      ) : query.isFetching && !query.isPending ? (
        <p className="mt-3 text-xs text-ink/55">
          正在读取当前筛选条件的最新研究页……
        </p>
      ) : null}
      {summary?.unavailable_reason ? (
        <p className="mt-6 text-sm text-ink/65">
          {leaderTacticsV2UnavailableText(summary.unavailable_reason)}
        </p>
      ) : null}

      {summary ? (
        <>
          <div className="mt-5 grid gap-3 sm:grid-cols-4">
            <div className="rounded-md bg-paper p-3">
              <p className="text-xs text-ink/55">筛选观测数</p>
              <p className="mt-1 font-mono text-lg">
                {summary.observation_count}
              </p>
            </div>
            <div className="rounded-md bg-paper p-3">
              <p className="text-xs text-ink/55">可用数</p>
              <p className="mt-1 font-mono text-lg">
                {summary.available_count}
              </p>
            </div>
            <div className="rounded-md bg-paper p-3">
              <p className="text-xs text-ink/55">通过门槛数</p>
              <p className="mt-1 font-mono text-lg">
                {summary.qualifying_count}
              </p>
            </div>
            <div className="rounded-md bg-paper p-3">
              <p className="text-xs text-ink/55">已加载</p>
              <p className="mt-1 font-mono text-lg">{candidates.length}</p>
            </div>
          </div>

          <div className="mt-3 rounded-md border border-border p-3 text-xs text-ink/65">
            <p>
              物化 manifest：
              <span className="break-all font-mono">
                {firstPage?.manifest_hash ?? "暂无"}
              </span>
            </p>
            <p className="mt-1">
              决策截止：{firstPage?.manifest_decision_cutoff ?? "暂无"} · 覆盖：
              {summary.coverage}
            </p>
            {exclusionEntries.length ? (
              <p className="mt-1">
                排除证据：
                {exclusionEntries
                  .slice(0, 6)
                  .map(([reason, count]) => `${reason}（${count}）`)
                  .join("、")}
              </p>
            ) : null}
          </div>

          <div className="mt-5 space-y-3">
            {candidates.map((candidate) => (
              <details
                key={`${candidate.manifest_hash}-${candidate.asset_code}-${candidate.formula_id}-${candidate.signal_date}`}
                className="rounded-md border border-border p-4"
              >
                <summary className="cursor-pointer list-none">
                  <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
                    <div>
                      <p className="font-semibold text-ink">
                        {candidate.asset_code} · {candidate.asset_name}
                      </p>
                      <p className="mt-1 text-xs text-ink/55">
                        {candidate.theme ?? "未提供主题"} ·{" "}
                        {candidate.formula_id} · {candidate.state}
                      </p>
                    </div>
                    <div className="text-left md:text-right">
                      <p className="font-mono text-sm">
                        {candidate.score === null
                          ? "暂无分数"
                          : candidate.score.toFixed(4)}
                      </p>
                      <p className="text-xs text-ink/55">
                        信号 {candidate.signal_date}
                      </p>
                    </div>
                  </div>
                </summary>
                <div className="mt-3 grid gap-3 border-t border-border pt-3 text-xs text-ink/70 md:grid-cols-2">
                  <div>
                    <p className="font-semibold text-ink">门槛与可用性</p>
                    <p className="mt-1">
                      可用性：{candidate.availability} · 门槛：
                      {candidate.qualifies ? "通过" : "未通过"}
                    </p>
                    <pre className="mt-1 overflow-auto whitespace-pre-wrap">
                      {JSON.stringify(candidate.gate_facts, null, 2)}
                    </pre>
                  </div>
                  <div>
                    <p className="font-semibold text-ink">来源与执行隔离</p>
                    <p className="mt-1">
                      排除：
                      {candidate.exclusion_reasons.length
                        ? candidate.exclusion_reasons.join("、")
                        : "无"}
                    </p>
                    <p className="mt-2">截止：{candidate.source_cutoff}</p>
                    <p>transition：{candidate.transition_date ?? "暂无"}</p>
                    <p className="mt-1">
                      通知：
                      {provenanceValue(
                        candidate.provenance,
                        "notification_provenance"
                      )}{" "}
                      · 执行：
                      {provenanceValue(
                        candidate.provenance,
                        "execution_provenance"
                      )}
                    </p>
                    <p className="mt-2 break-all">
                      manifest：{candidate.manifest_hash}
                    </p>
                  </div>
                </div>
              </details>
            ))}
          </div>

          {query.hasNextPage ? (
            <button
              type="button"
              className="mt-5 rounded-md border border-border px-4 py-2 text-sm text-ink disabled:cursor-not-allowed disabled:opacity-50"
              onClick={() => query.fetchNextPage()}
              disabled={query.isFetchingNextPage}
            >
              {query.isFetchingNextPage ? "正在加载下一页……" : "加载更多"}
            </button>
          ) : candidates.length ? (
            <p className="mt-5 text-center text-xs text-ink/55">
              已加载全部符合条件的候选。
            </p>
          ) : null}
        </>
      ) : null}
    </Panel>
  );
}
