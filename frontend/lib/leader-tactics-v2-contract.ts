export type Universe = "etf" | "ashare";
export type Formula =
  "all" | "breakout" | "base_launch" | "former_leader_repair";
export type Lifecycle = "all" | "preparing" | "confirmed" | "invalidated";

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
  availability: string;
  qualifies: boolean;
  score: number | null;
  signal_date: string;
  transition_date: string | null;
  source_cutoff: string;
  gate_facts: Record<string, unknown>;
  exclusion_reasons: string[];
  provenance: Record<string, unknown>;
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
  };
  ranking_source_kind: "research_replay";
  notification_provenance: "none";
  execution_provenance: "none";
  research_only: true;
  production_mutation_allowed: false;
};

export type LeaderTacticsV2HttpGet = (
  path: string,
  config: { signal: AbortSignal }
) => Promise<{ data: CandidatesResponse }>;

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
  leader_tactics_v2_not_materialized: "尚未生成可复现的研究物化结果。",
  leader_tactics_v2_empty_materialization: "最新物化结果没有候选观测。",
  leader_tactics_v2_invalid_filter: "筛选条件或分页游标无效。",
  economic_validation_not_materialized: "经济验证证据尚未物化。"
};

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
    page.ranking_source_kind !== "research_replay" ||
    page.notification_provenance !== "none" ||
    page.execution_provenance !== "none"
  ) {
    throw new Error("leader-tactics-v2 response has invalid provenance");
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
      candidate.provenance.research_only !== true ||
      candidate.provenance.notification_provenance !== "none" ||
      candidate.provenance.execution_provenance !== "none"
    ) {
      throw new Error("leader-tactics-v2 candidate has invalid provenance");
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
