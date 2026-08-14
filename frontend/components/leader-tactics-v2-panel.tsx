"use client";

import {
  useInfiniteQuery,
  useMutation,
  useQueryClient
} from "@tanstack/react-query";
import { useState } from "react";

import { Panel } from "@/components/ui";
import { api } from "@/lib/api";
import {
  type Candidate,
  type CandidatesResponse,
  type LeaderTrackingSourceMode,
  LEADER_TACTICS_V2_RESEARCH_COPY,
  buildLeaderCandidateTrackingPayload,
  effectiveLeaderCandidateState,
  fetchLeaderTacticsV2Page,
  leaderTacticsV2QueryKey,
  leaderCandidateTrackingProvenance,
  leaderCandidateTrackingProvenanceText,
  leaderTacticsTrackingErrorText,
  leaderTacticsV2UnavailableText,
  mergeLeaderTacticsV2Pages,
  sentimentActionLabel,
  sentimentRiskLabel
} from "@/lib/leader-tactics-v2-contract";

type Universe = "etf" | "ashare";
type Formula = "all" | "breakout" | "base_launch" | "former_leader_repair";
type Lifecycle =
  | "all"
  | "preparing"
  | "turning_watch"
  | "confirmed"
  | "invalidated";

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
  all: "正式候选",
  preparing: "准备中",
  turning_watch: "转强观察",
  confirmed: "已确认",
  invalidated: "已失效"
};

function provenanceValue(provenance: Record<string, unknown>, key: string) {
  const value = provenance[key];
  return typeof value === "string" && value ? value : "none";
}

function todayInputValue() {
  const today = new Date();
  const month = String(today.getMonth() + 1).padStart(2, "0");
  const day = String(today.getDate()).padStart(2, "0");
  return `${today.getFullYear()}-${month}-${day}`;
}

function optionalPositiveNumber(value: string) {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function LeaderCandidateTrackingForm({
  candidate,
  manifestHash,
  decisionCutoff
}: {
  candidate: Candidate;
  manifestHash: string | null;
  decisionCutoff: string | null;
}) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [buyDate, setBuyDate] = useState(todayInputValue());
  const [buyAmount, setBuyAmount] = useState("3000");
  const [orderTimeBucket, setOrderTimeBucket] = useState<
    "before_15" | "after_15" | "unknown"
  >("unknown");
  const [confirmedNavDate, setConfirmedNavDate] = useState("");
  const [confirmedNav, setConfirmedNav] = useState("");
  const [confirmedShares, setConfirmedShares] = useState("");
  const [note, setNote] = useState("");
  const [success, setSuccess] = useState(false);
  const provenance = leaderCandidateTrackingProvenance(
    candidate,
    manifestHash,
    decisionCutoff
  );
  const [sourceMode, setSourceMode] = useState<LeaderTrackingSourceMode>(() =>
    provenance.kind === "candidate_backed"
      ? "candidate_backed"
      : "manual_selection"
  );
  const createTracking = useMutation({
    mutationFn: async () => {
      const amount = optionalPositiveNumber(buyAmount);
      if (!buyDate || amount === null) {
        throw new Error("请填写有效的买入日期和买入金额。");
      }
      if (
        confirmedNav.trim() &&
        optionalPositiveNumber(confirmedNav) === null
      ) {
        throw new Error("实际成交价必须是大于 0 的数字。");
      }
      if (
        confirmedShares.trim() &&
        optionalPositiveNumber(confirmedShares) === null
      ) {
        throw new Error("实际份额必须是大于 0 的数字。");
      }
      const payload = buildLeaderCandidateTrackingPayload(
        candidate,
        manifestHash,
        decisionCutoff,
        {
          buyDate,
          buyAmount: amount,
          orderTimeBucket,
          confirmedNavDate,
          confirmedNav: optionalPositiveNumber(confirmedNav),
          confirmedShares: optionalPositiveNumber(confirmedShares),
          note
        },
        sourceMode
      );
      return (await api.post("/api/tracked-positions", payload)).data;
    },
    onSuccess: async () => {
      setSuccess(true);
      setOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["tracked-positions"] });
    }
  });

  if (effectiveLeaderCandidateState(candidate) === "invalidated") {
    return (
      <p className="mt-3 rounded bg-ink/5 px-3 py-2 text-xs leading-5 text-ink/55">
        当前候选已失效，不提供追踪入口；请等待新的龙头候选周期。
      </p>
    );
  }

  return (
    <div className="mt-3 rounded border border-accent/20 bg-accent/5 p-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="font-semibold text-ink">已买入，开始追踪</p>
          <p className="mt-1 text-xs leading-5 text-ink/60">
            龙头战法（仅卖出提醒）：候选和入场只在网页显示，不发送候选或买入邮件。
          </p>
          <p className="mt-1 text-xs leading-5 text-ink/60">
            {leaderCandidateTrackingProvenanceText(provenance)}
          </p>
        </div>
        <button
          type="button"
          className="rounded-md bg-accent px-3 py-2 text-xs font-semibold text-white transition hover:bg-accent/90"
          onClick={() => {
            setSuccess(false);
            setOpen((value) => !value);
          }}
        >
          {open ? "收起录入" : "我已买入，开始追踪"}
        </button>
      </div>
      {open ? (
        <div className="mt-3 grid gap-2 border-t border-accent/15 pt-3 sm:grid-cols-2 lg:grid-cols-4">
          <label className="text-xs text-ink/70 sm:col-span-2 lg:col-span-4">
            研究来源绑定
            <select
              className="mt-1 w-full rounded border border-border bg-white px-2 py-2 text-sm text-ink"
              value={sourceMode}
              onChange={(event) =>
                setSourceMode(event.target.value as LeaderTrackingSourceMode)
              }
            >
              <option
                value="candidate_backed"
                disabled={provenance.kind !== "candidate_backed"}
              >
                {provenance.kind === "candidate_backed"
                  ? "绑定已确认候选来源（默认）"
                  : "绑定已确认候选来源（当前不可用）"}
              </option>
              <option value="manual_selection">
                不绑定研究来源，按手动选择
              </option>
            </select>
            <span className="mt-1 block leading-5 text-ink/55">
              {sourceMode === "candidate_backed"
                ? "默认优先绑定 confirmed 候选；后端仍会校验 manifest 与决策截止时间。"
                : "本次不会提交 source_manifest_hash/source_decision_at，也不会声称由研究候选生成。"}
            </span>
          </label>
          <label className="text-xs text-ink/70">
            买入日期
            <input
              className="mt-1 w-full rounded border border-border bg-white px-2 py-2 text-sm text-ink"
              type="date"
              value={buyDate}
              onChange={(event) => setBuyDate(event.target.value)}
            />
          </label>
          <label className="text-xs text-ink/70">
            买入金额
            <input
              className="mt-1 w-full rounded border border-border bg-white px-2 py-2 text-sm text-ink"
              inputMode="decimal"
              value={buyAmount}
              onChange={(event) => setBuyAmount(event.target.value)}
            />
          </label>
          <label className="text-xs text-ink/70">
            下单时间
            <select
              className="mt-1 w-full rounded border border-border bg-white px-2 py-2 text-sm text-ink"
              value={orderTimeBucket}
              onChange={(event) =>
                setOrderTimeBucket(
                  event.target.value as "before_15" | "after_15" | "unknown"
                )
              }
            >
              <option value="before_15">15:00 前</option>
              <option value="after_15">15:00 后</option>
              <option value="unknown">不确定</option>
            </select>
          </label>
          <label className="text-xs text-ink/70">
            成交价日期（可选）
            <input
              className="mt-1 w-full rounded border border-border bg-white px-2 py-2 text-sm text-ink"
              type="date"
              value={confirmedNavDate}
              onChange={(event) => setConfirmedNavDate(event.target.value)}
            />
          </label>
          <label className="text-xs text-ink/70">
            实际成交价（可选）
            <input
              className="mt-1 w-full rounded border border-border bg-white px-2 py-2 text-sm text-ink"
              inputMode="decimal"
              value={confirmedNav}
              onChange={(event) => setConfirmedNav(event.target.value)}
            />
          </label>
          <label className="text-xs text-ink/70">
            实际份额（可选）
            <input
              className="mt-1 w-full rounded border border-border bg-white px-2 py-2 text-sm text-ink"
              inputMode="decimal"
              value={confirmedShares}
              onChange={(event) => setConfirmedShares(event.target.value)}
            />
          </label>
          <label className="text-xs text-ink/70 sm:col-span-2">
            备注（可选）
            <input
              className="mt-1 w-full rounded border border-border bg-white px-2 py-2 text-sm text-ink"
              placeholder="例如：证券账户手动买入"
              value={note}
              onChange={(event) => setNote(event.target.value)}
            />
          </label>
          <div className="sm:col-span-2 lg:col-span-4">
            <p className="text-xs leading-5 text-ink/55">
              标的类型：
              {candidate.universe === "ashare" ? "A 股个股" : "场内 ETF"}
              ；策略仅产生卖出提醒，不会自动成交。
            </p>
            <button
              type="button"
              className="mt-2 rounded-md bg-ink px-3 py-2 text-xs font-semibold text-white transition hover:bg-ink/90 disabled:opacity-60"
              disabled={createTracking.isPending}
              onClick={() => createTracking.mutate()}
            >
              {createTracking.isPending
                ? "保存中..."
                : "确认买入信息并开始追踪"}
            </button>
          </div>
        </div>
      ) : null}
      {success ? (
        <p className="mt-2 rounded bg-emerald-50 px-3 py-2 text-xs text-emerald-800">
          已创建手动持仓追踪；后续只按龙头战法卖出规则评估。
        </p>
      ) : null}
      {createTracking.isError ? (
        <div className="mt-2 rounded bg-rose-50 px-3 py-2 text-xs leading-5 text-rose-800">
          <p>{leaderTacticsTrackingErrorText(createTracking.error)}</p>
          {sourceMode === "candidate_backed" ? (
            <button
              type="button"
              className="mt-2 rounded border border-rose-300 px-2 py-1 font-semibold text-rose-900"
              onClick={() => {
                createTracking.reset();
                setSourceMode("manual_selection");
              }}
            >
              切换为手动选择后重试
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function LeaderTacticsV2Panel() {
  const [universe, setUniverse] = useState<Universe>("ashare");
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
            {firstPage?.ranking_source_kind === "post_close_watchlist"
              ? "来源：盘后观察榜"
              : LEADER_TACTICS_V2_RESEARCH_COPY.source}
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
            {firstPage?.decision_mode === "post_close_watchlist" ? (
              <p className="mt-1">
                盘后观察榜：特征日 {firstPage.feature_trade_date ?? "暂无"} ·
                成员评估日 {firstPage.membership_evaluation_date ?? "暂无"} ·
                最早可评估交易日 {firstPage.next_eligible_date ?? "暂无"} ·
                不计入历史 PIT 验证
              </p>
            ) : null}
            {summary.materialization_progress ? (
              <p className="mt-1">
                分批物化：{summary.materialization_progress.status} · 特征
                {summary.materialization_progress.completed_count}/
                {summary.materialization_progress.expected_count} · 已完成主题组
                {summary.materialization_progress.completed_group_count}
              </p>
            ) : null}
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
                      {candidate.universe === "ashare" ? (
                        <p className="mt-1 text-xs text-amber-800">
                          情绪风险：
                          {sentimentRiskLabel(candidate.sentiment_risk_state)} ·
                          动作：
                          {sentimentActionLabel(
                            candidate.sentiment_risk_action_mode
                          )}
                        </p>
                      ) : null}
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
                    {candidate.universe === "ashare" ? (
                      <p className="mt-2 rounded bg-amber-50 px-2 py-1 text-amber-900">
                        情绪覆盖：
                        {sentimentRiskLabel(
                          candidate.sentiment_risk_state
                        )} ·{" "}
                        {sentimentActionLabel(
                          candidate.sentiment_risk_action_mode
                        )}
                        {candidate.sentiment_risk_state === "unavailable"
                          ? `（${String(
                              candidate.sentiment_risk_provenance
                                .unavailable_reason ?? "原因未提供"
                            )}）`
                          : ""}
                      </p>
                    ) : null}
                    {candidate.state === "turning_watch" ? (
                      <p className="mt-2 rounded bg-amber-50 px-2 py-1 text-amber-900">
                        仅为非行动“转强观察”，距正式候选仍缺：
                        {String(
                          candidate.gate_facts.turning_watch_formal_blockers ??
                            candidate.gate_facts
                              .turning_watch_missing_conditions ??
                            "未量化"
                        )}
                      </p>
                    ) : null}
                    <p className="mt-2">
                      主题层级：
                      {String(
                        candidate.gate_facts.theme_hierarchy_level ?? "未知"
                      )}{" "}
                      · 解析：
                      {String(
                        candidate.gate_facts.theme_resolution_mode ?? "未知"
                      )}
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
                <LeaderCandidateTrackingForm
                  candidate={candidate}
                  manifestHash={firstPage?.manifest_hash ?? null}
                  decisionCutoff={firstPage?.manifest_decision_cutoff ?? null}
                />
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
