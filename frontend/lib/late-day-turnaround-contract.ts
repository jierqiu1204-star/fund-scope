export type LateDayUniverse = "etf" | "ashare";
export type LateDayKind = "formal_candidate" | "daily_proxy_watchlist";

export type LateDayItem = {
  asset_code: string;
  asset_name: string;
  observation_kind: string;
  available: boolean;
  reason: string;
  score: number | null;
  ma5: number | null;
  gain_pct: number | null;
  ma_deviation_pct: number | null;
  amount_ratio: number | null;
  signal_date: string;
  decision_at: string;
  provenance_json: Record<string, unknown>;
};

export type LateDayResponse = {
  universe: LateDayUniverse;
  decision_at: string | null;
  manifest_hash: string | null;
  status: string;
  summary: {
    expected_count: number;
    evaluated_count: number;
    available_count: number;
    qualifying_count: number;
    coverage_ratio: number;
    exclusion_counts: Record<string, number>;
    unavailable_reason: string | null;
  };
  items: LateDayItem[];
  next_cursor: number | null;
  has_more: boolean;
  ranking_source_kind: "research_shadow";
  notification_provenance: "none";
  execution_provenance: "none";
  research_only: true;
  production_mutation_allowed: false;
};

export const unavailableText: Record<string, string> = {
  late_day_turnaround_api_disabled: "尾盘研究 API 尚未启用。",
  late_day_turnaround_not_materialized: "尚无兼容的已物化尾盘研究证据。",
  minute_data_unavailable: "缺少截止时点前真实可见的分钟数据，不能生成正式候选。",
  incomplete_declared_coverage: "声明标的池的盘中覆盖不完整，已禁止发布部分候选。",
  job_timeout: "本次物化达到 55 秒硬超时，未发布部分结果。"
};
