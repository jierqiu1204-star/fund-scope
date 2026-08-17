export type Universe = "etf" | "ashare";
export type Formula =
  | "all"
  | "breakout"
  | "base_launch"
  | "former_leader_repair"
  | "low_base_catchup";
export type Lifecycle =
  | "all"
  | "preparing"
  | "turning_watch"
  | "confirmed"
  | "invalidated";

export type SentimentRiskState =
  | "healthy"
  | "warning"
  | "risk_off"
  | "unavailable"
  | "not_applicable";
export type SentimentActionMode =
  | "shadow_entry_allowed"
  | "observe_only"
  | "not_applicable";

export type LeaderTacticsV2Filters = {
  universe: Universe;
  formula: Formula;
  state: Lifecycle;
  asOf: string;
};

export type Candidate = {
  manifest_hash: string;
  universe: Universe;
  asset_code: string;
  asset_name: string;
  theme: string | null;
  sector: string | null;
  tracked_index: string | null;
  formula_id: string;
  state: Exclude<Lifecycle, "all">;
  effective_state?: Exclude<Lifecycle, "all">;
  availability: string;
  qualifies: boolean;
  entry_status: "watch" | "actionable" | "overextended" | "invalidated";
  score: number | null;
  signal_date: string;
  transition_date: string | null;
  source_cutoff: string;
  gate_facts: Record<string, unknown>;
  exclusion_reasons: string[];
  provenance: Record<string, unknown>;
  feature_hash: string;
  sentiment_risk_state: SentimentRiskState;
  sentiment_risk_action_mode: SentimentActionMode;
  sentiment_risk_new_entry_allowed: boolean | null;
  sentiment_risk_provenance: Record<string, unknown>;
};

export type CandidatesResponse = {
  schema_version: string;
  experiment_family: string;
  universe: Universe;
  formula: string;
  state: string;
  as_of: string | null;
  manifest_hash: string | null;
  manifest_decision_cutoff: string | null;
  decision_mode: "session_pit" | "post_close_watchlist" | null;
  feature_trade_date: string | null;
  membership_evaluation_date: string | null;
  next_eligible_date: string | null;
  historical_validation_eligible: boolean | null;
  candidates: Candidate[];
  next_cursor: string | null;
  has_more: boolean;
  summary: {
    observation_count: number;
    available_count: number;
    qualifying_count: number;
    returned_count: number;
    coverage: string;
    unavailable_reason: string | null;
    exclusion_counts: Record<string, number>;
    manifest_hash: string | null;
    sentiment_risk: {
      observation_count: number;
      state_counts: Record<string, number>;
      action_mode_counts: Record<string, number>;
      new_entry_allowed_count: number;
      unavailable_reasons: Record<string, number>;
    };
    materialization_progress?: {
      run_hash: string;
      status: string;
      signal_date: string;
      source_cutoff: string;
      expected_count: number;
      completed_count: number;
      completed_group_count: number;
      updated_at: string;
    } | null;
  };
  ranking_source_kind: "research_replay" | "post_close_watchlist";
  notification_provenance: "none";
  execution_provenance: "none";
  research_only: true;
  production_mutation_allowed: false;
};

export type LeaderTacticsV2HttpGet = (
  path: string,
  config: { signal: AbortSignal }
) => Promise<{ data: CandidatesResponse }>;

export type LeaderTrackingAssetType = "etf" | "stock";

export type LeaderTrackingSourceMode = "candidate_backed" | "manual_selection";

export type LeaderCandidateTrackingFormValues = {
  buyDate: string;
  buyAmount: number;
  orderTimeBucket: "before_15" | "after_15" | "unknown";
  confirmedNavDate: string;
  confirmedNav: number | null;
  confirmedShares: number | null;
  note: string;
};

export type LeaderCandidateTrackingPayload = {
  asset_type: LeaderTrackingAssetType;
  asset_code: string;
  buy_amount: number;
  buy_date?: string;
  order_time_bucket: "before_15" | "after_15" | "unknown";
  confirmed_nav_date?: string;
  confirmed_nav?: number;
  confirmed_shares?: number;
  note?: string;
  alert_policy_id: "leader_tactics_exit_v1";
  source_manifest_hash?: string;
  source_decision_at?: string;
};

export type LeaderCandidateTrackingProvenance = {
  kind: "candidate_backed" | "manual_selection";
  source_manifest_hash: string | null;
  source_decision_at: string | null;
  reason:
    | "candidate_backed"
    | "not_confirmed"
    | "not_qualifying"
    | "missing_manifest_hash"
    | "missing_decision_cutoff";
};

export const LEADER_TACTICS_EXIT_POLICY_COPY = {
  label: "龙头战法（仅卖出提醒）",
  description:
    "候选和入场只在网页显示；用户确认买入后，仅在合格复权日线触发灾难止损、保本线或收盘跌破五日线的卖出提醒。",
  researchBoundary:
    "这是用户手动录入持仓，不会发送候选摘要/买入邮件，不会自动建仓、成交或修改综合排名。"
} as const;

export function effectiveLeaderCandidateState(candidate: Candidate) {
  return candidate.effective_state ?? candidate.state;
}

function normalizeLeaderDecisionCutoff(value: string | null | undefined) {
  const trimmed = value?.trim() ?? "";
  if (!trimmed) return null;
  if (/([zZ]|[+-]\d{2}:?\d{2})$/.test(trimmed)) return trimmed;
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(trimmed)) {
    return `${trimmed}+08:00`;
  }
  return null;
}

export function leaderCandidateTrackingProvenance(
  candidate: Candidate,
  manifestHash: string | null | undefined,
  decisionCutoff: string | null | undefined
): LeaderCandidateTrackingProvenance {
  const sourceManifestHash =
    candidate.manifest_hash || manifestHash?.trim() || null;
  const sourceDecisionAt = normalizeLeaderDecisionCutoff(decisionCutoff);
  const state = effectiveLeaderCandidateState(candidate);
  if (state !== "confirmed") {
    return {
      kind: "manual_selection",
      source_manifest_hash: null,
      source_decision_at: null,
      reason: "not_confirmed"
    };
  }
  if (!candidate.qualifies) {
    return {
      kind: "manual_selection",
      source_manifest_hash: null,
      source_decision_at: null,
      reason: "not_qualifying"
    };
  }
  if (!sourceManifestHash) {
    return {
      kind: "manual_selection",
      source_manifest_hash: null,
      source_decision_at: null,
      reason: "missing_manifest_hash"
    };
  }
  if (!sourceDecisionAt) {
    return {
      kind: "manual_selection",
      source_manifest_hash: null,
      source_decision_at: null,
      reason: "missing_decision_cutoff"
    };
  }
  return {
    kind: "candidate_backed",
    source_manifest_hash: sourceManifestHash,
    source_decision_at: sourceDecisionAt,
    reason: "candidate_backed"
  };
}

export function leaderCandidateTrackingProvenanceText(
  provenance: LeaderCandidateTrackingProvenance
) {
  if (provenance.kind === "candidate_backed") {
    return "来源：已确认龙头候选（manifest 与决策截止时间完整，将由后端再次校验）";
  }
  switch (provenance.reason) {
    case "not_confirmed":
      return "来源：手动选择；当前候选尚未确认，不绑定研究来源。";
    case "not_qualifying":
      return "来源：手动选择；当前观测未通过正式候选门槛，不绑定研究来源。";
    case "missing_manifest_hash":
      return "来源：手动选择；研究 manifest 不完整，不伪造候选来源。";
    case "missing_decision_cutoff":
      return "来源：手动选择；研究决策截止时间缺失，不伪造候选来源。";
    default:
      return "来源：手动选择；未绑定研究候选来源。";
  }
}

export function buildLeaderCandidateTrackingPayload(
  candidate: Candidate,
  manifestHash: string | null | undefined,
  decisionCutoff: string | null | undefined,
  values: LeaderCandidateTrackingFormValues,
  sourceMode: LeaderTrackingSourceMode = "candidate_backed"
): LeaderCandidateTrackingPayload {
  const provenance = leaderCandidateTrackingProvenance(
    candidate,
    manifestHash,
    decisionCutoff
  );
  if (
    sourceMode === "candidate_backed" &&
    provenance.kind !== "candidate_backed"
  ) {
    throw new Error(
      "当前候选来源不可绑定，请明确选择不绑定研究来源，按手动选择后再重试。"
    );
  }
  const payload: LeaderCandidateTrackingPayload = {
    asset_type: candidate.universe === "ashare" ? "stock" : "etf",
    asset_code: candidate.asset_code,
    buy_amount: values.buyAmount,
    buy_date: values.buyDate || undefined,
    order_time_bucket: values.orderTimeBucket,
    confirmed_nav_date: values.confirmedNavDate || undefined,
    confirmed_nav: values.confirmedNav ?? undefined,
    confirmed_shares: values.confirmedShares ?? undefined,
    note: values.note.trim() || undefined,
    alert_policy_id: "leader_tactics_exit_v1"
  };
  if (
    sourceMode === "candidate_backed" &&
    provenance.kind === "candidate_backed"
  ) {
    payload.source_manifest_hash = provenance.source_manifest_hash ?? undefined;
    payload.source_decision_at = provenance.source_decision_at ?? undefined;
  }
  return payload;
}

export function leaderTacticsTrackingErrorText(error: unknown) {
  const detail =
    typeof error === "object" && error !== null && "response" in error
      ? (error as { response?: { data?: { detail?: unknown } } }).response?.data
          ?.detail
      : null;
  const raw = typeof detail === "string" ? detail : "";
  if (raw.includes("不支持") || raw.includes("只支持")) {
    return "当前标的类型不能使用龙头战法邮件规则，请选择 ETF 或个股。";
  }
  if (
    raw.includes("来源") ||
    raw.includes("manifest") ||
    raw.includes("截止")
  ) {
    return "候选来源校验失败，已停止创建；请在表单中明确选择“不绑定研究来源，按手动选择”后再重试，系统不会自动降级。";
  }
  if (raw.includes("数据") || raw.includes("复权") || raw.includes("调整")) {
    return "当前缺少合格复权日线证据，暂时不能建立可验证的龙头战法追踪。";
  }
  if (raw.includes("登录") || raw.includes("权限")) {
    return "登录状态或权限已失效，请重新登录后再创建追踪。";
  }
  return "创建龙头战法追踪失败，请检查买入信息后重试。";
}

export const LEADER_TACTICS_V2_PATH =
  "/api/short-research/leader-tactics-v2/candidates";

export const LEADER_TACTICS_V2_RESEARCH_COPY = {
  eyebrow: "Research only",
  source: "来源：研究回放",
  notification: "通知：无",
  execution: "执行：无",
  disclaimer:
    "这是透明工程代理，不是文章作者未公开的“起飞信号”，不会改变正式综合排名、持仓、邮件或执行。",
  evidenceBoundary:
    "文章披露条件、透明代理、历史回放、通知送达和用户成交是不同证据层；缺数据时不从另一标的池或原始价格回填。更换标的池、公式、生命周期或截止日期会重置已加载分页。"
} as const;

const unavailableLabels: Record<string, string> = {
  leader_tactics_v2_api_disabled: "V2 研究 API 仍处于关闭状态。",
  leader_tactics_v2_etf_materialization_disabled:
    "ETF 龙头研究尚未开启物化；个股研究与 ETF 综合排名不受影响。",
  leader_tactics_v2_not_materialized: "尚未生成可复现的研究物化结果。",
  leader_tactics_v2_empty_materialization: "最新物化结果没有候选观测。",
  leader_tactics_v2_invalid_filter: "筛选条件或分页游标无效。",
  economic_validation_not_materialized: "经济验证证据尚未物化。"
};

const sentimentRiskLabels: Record<SentimentRiskState, string> = {
  healthy: "情绪健康",
  warning: "情绪预警",
  risk_off: "情绪风险关闭",
  unavailable: "情绪证据不可用",
  not_applicable: "不适用"
};

const sentimentActionLabels: Record<SentimentActionMode, string> = {
  shadow_entry_allowed: "研究准入（非实盘）",
  observe_only: "仅观察",
  not_applicable: "不适用"
};

export function sentimentRiskLabel(state: SentimentRiskState) {
  return sentimentRiskLabels[state];
}

export function sentimentActionLabel(mode: SentimentActionMode) {
  return sentimentActionLabels[mode];
}

export function leaderTacticsV2QueryKey(filters: LeaderTacticsV2Filters) {
  return [
    "leader-tactics-v2",
    filters.universe,
    filters.formula,
    filters.state,
    filters.asOf
  ] as const;
}

export function buildLeaderTacticsV2Url(
  filters: LeaderTacticsV2Filters,
  cursor: string | null = null
) {
  const params = new URLSearchParams({
    universe: filters.universe,
    formula: filters.formula,
    state: filters.state,
    limit: "50"
  });
  if (filters.asOf) params.set("as_of", filters.asOf);
  if (cursor) params.set("cursor", cursor);
  return `${LEADER_TACTICS_V2_PATH}?${params.toString()}`;
}

export function getLeaderTacticsV2NextCursor(page: CandidatesResponse) {
  return page.has_more ? (page.next_cursor ?? undefined) : undefined;
}

function containsRawPriceField(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  if (Array.isArray(value)) return value.some(containsRawPriceField);
  return Object.entries(value).some(
    ([key, nested]) =>
      /(^|[_-])raw([_-]?price)($|[_-])/i.test(key) ||
      containsRawPriceField(nested)
  );
}

function isExpectedDate(asOf: string, responseAsOf: string | null) {
  return !asOf || (responseAsOf ? responseAsOf.slice(0, 10) === asOf : false);
}

export function assertLeaderTacticsV2PageContract(
  page: CandidatesResponse,
  filters: LeaderTacticsV2Filters
) {
  if (page.universe !== filters.universe) {
    throw new Error("leader-tactics-v2 response crossed universe boundary");
  }
  if (page.formula !== filters.formula || page.state !== filters.state) {
    throw new Error("leader-tactics-v2 response crossed filter boundary");
  }
  if (!isExpectedDate(filters.asOf, page.as_of)) {
    throw new Error("leader-tactics-v2 response crossed as_of boundary");
  }
  if (
    page.research_only !== true ||
    page.production_mutation_allowed !== false
  ) {
    throw new Error("leader-tactics-v2 response is not research-only");
  }
  if (
    !["research_replay", "post_close_watchlist"].includes(
      page.ranking_source_kind
    ) ||
    page.notification_provenance !== "none" ||
    page.execution_provenance !== "none"
  ) {
    throw new Error("leader-tactics-v2 response has invalid provenance");
  }
  if (
    page.ranking_source_kind === "post_close_watchlist" &&
    (page.decision_mode !== "post_close_watchlist" ||
      !page.feature_trade_date ||
      !page.membership_evaluation_date ||
      !page.next_eligible_date ||
      page.historical_validation_eligible !== false)
  ) {
    throw new Error("leader-tactics-v2 post-close timing is incomplete");
  }
  if (containsRawPriceField(page)) {
    throw new Error("leader-tactics-v2 response contains a raw-price fallback");
  }
  for (const candidate of page.candidates) {
    if (candidate.universe !== filters.universe) {
      throw new Error("leader-tactics-v2 candidate crossed universe boundary");
    }
    if (filters.state !== "all" && candidate.state !== filters.state) {
      throw new Error("leader-tactics-v2 candidate crossed state boundary");
    }
    if (
      candidate.state === "turning_watch" &&
      (candidate.qualifies !== false || candidate.score !== null)
    ) {
      throw new Error(
        "leader-tactics-v2 turning watch must remain non-actionable"
      );
    }
    if (
      candidate.provenance.research_only !== true ||
      candidate.provenance.notification_provenance !== "none" ||
      candidate.provenance.execution_provenance !== "none"
    ) {
      throw new Error("leader-tactics-v2 candidate has invalid provenance");
    }
    if (
      ![
        "healthy",
        "warning",
        "risk_off",
        "unavailable",
        "not_applicable"
      ].includes(candidate.sentiment_risk_state) ||
      !["shadow_entry_allowed", "observe_only", "not_applicable"].includes(
        candidate.sentiment_risk_action_mode
      )
    ) {
      throw new Error("leader-tactics-v2 candidate has invalid sentiment risk");
    }
    if (candidate.universe === "etf") {
      if (
        candidate.sentiment_risk_state !== "not_applicable" ||
        candidate.sentiment_risk_action_mode !== "not_applicable" ||
        candidate.sentiment_risk_new_entry_allowed !== null
      ) {
        throw new Error(
          "ETF candidate crossed A-share sentiment risk boundary"
        );
      }
    } else if (candidate.formula_id === "leader_breakout_proxy_v2") {
      const blocked = ["warning", "risk_off", "unavailable"].includes(
        candidate.sentiment_risk_state
      );
      if (
        (!candidate.qualifies &&
          (candidate.sentiment_risk_action_mode !== "observe_only" ||
            candidate.sentiment_risk_new_entry_allowed !== false)) ||
        (blocked &&
          (candidate.sentiment_risk_action_mode !== "observe_only" ||
            candidate.sentiment_risk_new_entry_allowed !== false)) ||
        (candidate.qualifies &&
          !blocked &&
          candidate.sentiment_risk_state === "healthy" &&
          (candidate.sentiment_risk_action_mode !== "shadow_entry_allowed" ||
            candidate.sentiment_risk_new_entry_allowed !== true))
      ) {
        throw new Error("A-share breakout sentiment action mapping is invalid");
      }
    } else if (
      candidate.sentiment_risk_action_mode !== "not_applicable" ||
      candidate.sentiment_risk_new_entry_allowed !== null
    ) {
      throw new Error("non-breakout candidate has a sentiment action override");
    }
  }
  return page;
}

export async function fetchLeaderTacticsV2Page(
  request: LeaderTacticsV2HttpGet,
  filters: LeaderTacticsV2Filters,
  cursor: string | null,
  signal: AbortSignal
) {
  if (signal.aborted)
    throw new DOMException("The request was aborted", "AbortError");
  const response = await request(buildLeaderTacticsV2Url(filters, cursor), {
    signal
  });
  if (signal.aborted)
    throw new DOMException("The request was aborted", "AbortError");
  return assertLeaderTacticsV2PageContract(response.data, filters);
}

export function mergeLeaderTacticsV2Pages(
  pages: readonly CandidatesResponse[]
) {
  if (pages.length <= 1) return pages[0]?.candidates ?? [];
  const first = pages[0];
  for (const page of pages.slice(1)) {
    if (
      page.universe !== first.universe ||
      page.formula !== first.formula ||
      page.state !== first.state ||
      page.as_of !== first.as_of
    ) {
      throw new Error(
        "leader-tactics-v2 pages are not from one filter snapshot"
      );
    }
  }
  return pages.flatMap((page) => page.candidates);
}

export function leaderTacticsV2UnavailableText(
  reason: string | null | undefined
) {
  return reason ? (unavailableLabels[reason] ?? reason) : "暂无可用研究证据。";
}
