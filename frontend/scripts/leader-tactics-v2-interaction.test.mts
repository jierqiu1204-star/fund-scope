import assert from "node:assert/strict";
import test from "node:test";

import {
  type CandidatesResponse,
  type LeaderTacticsV2Filters,
  LEADER_TACTICS_V2_RESEARCH_COPY,
  assertLeaderTacticsV2PageContract,
  buildLeaderTacticsV2Url,
  fetchLeaderTacticsV2Page,
  getLeaderTacticsV2NextCursor,
  leaderTacticsV2QueryKey,
  leaderTacticsV2UnavailableText,
  mergeLeaderTacticsV2Pages
} from "../lib/leader-tactics-v2-contract.ts";

const filters: LeaderTacticsV2Filters = {
  universe: "etf",
  formula: "all",
  state: "all",
  asOf: "2026-08-03"
};

function candidate(universe: "etf" | "ashare" = "etf") {
  return {
    manifest_hash: "manifest-1",
    universe,
    asset_code: "510300",
    asset_name: "沪深300ETF",
    theme: "宽基",
    sector: null,
    tracked_index: "000300",
    formula_id: "leader_breakout_proxy_v2",
    state: "preparing" as const,
    availability: "available",
    qualifies: true,
    score: 0.8,
    signal_date: "2026-08-03",
    transition_date: null,
    source_cutoff: "2026-08-03T15:00:00",
    gate_facts: { peer_count: 6 },
    exclusion_reasons: [],
    provenance: {
      research_only: true,
      notification_provenance: "none",
      execution_provenance: "none"
    }
  };
}

function page(overrides: Partial<CandidatesResponse> = {}): CandidatesResponse {
  return {
    schema_version: "dual_universe_leader_tactics_screen_v2",
    experiment_family: "dual_universe_leader_tactics_v2",
    universe: "etf",
    formula: "all",
    state: "all",
    as_of: "2026-08-03",
    manifest_hash: "manifest-1",
    manifest_decision_cutoff: "2026-08-03T15:00:00",
    decision_mode: "session_pit",
    feature_trade_date: "2026-08-03",
    membership_evaluation_date: "2026-08-03",
    next_eligible_date: null,
    historical_validation_eligible: true,
    candidates: [candidate()],
    next_cursor: "cursor-2",
    has_more: true,
    summary: {
      observation_count: 1,
      available_count: 1,
      qualifying_count: 1,
      returned_count: 1,
      coverage: "available",
      unavailable_reason: null,
      exclusion_counts: {},
      manifest_hash: "manifest-1"
    },
    ranking_source_kind: "research_replay",
    notification_provenance: "none",
    execution_provenance: "none",
    research_only: true,
    production_mutation_allowed: false,
    ...overrides
  };
}

test("filters isolate the universe and all request dimensions", () => {
  const ashareFilters = {
    ...filters,
    universe: "ashare" as const,
    formula: "breakout" as const,
    state: "confirmed" as const
  };
  const url = new URL(
    buildLeaderTacticsV2Url(ashareFilters, "cursor-1"),
    "https://local.test"
  );

  assert.deepEqual(
    [...leaderTacticsV2QueryKey(filters)],
    ["leader-tactics-v2", "etf", "all", "all", "2026-08-03"]
  );
  assert.notDeepEqual(
    leaderTacticsV2QueryKey(filters),
    leaderTacticsV2QueryKey(ashareFilters)
  );
  assert.equal(url.searchParams.get("universe"), "ashare");
  assert.equal(url.searchParams.get("formula"), "breakout");
  assert.equal(url.searchParams.get("state"), "confirmed");
  assert.equal(url.searchParams.get("as_of"), "2026-08-03");
  assert.equal(url.searchParams.get("cursor"), "cursor-1");
  assert.equal(url.searchParams.get("limit"), "50");
});

test("pagination only advances on an explicit cursor and keeps pages in one snapshot", () => {
  const first = page();
  const last = page({
    has_more: false,
    next_cursor: null,
    candidates: [candidate()]
  });

  assert.equal(getLeaderTacticsV2NextCursor(first), "cursor-2");
  assert.equal(getLeaderTacticsV2NextCursor(last), undefined);
  assert.equal(mergeLeaderTacticsV2Pages([first, last]).length, 2);
  assert.throws(
    () =>
      mergeLeaderTacticsV2Pages([
        first,
        page({ universe: "ashare", candidates: [candidate("ashare")] })
      ]),
    /one filter snapshot/
  );
});

test("provenance, research-only and no-fallback contracts are enforced", () => {
  const validPage = page();
  assert.equal(
    assertLeaderTacticsV2PageContract(validPage, filters),
    validPage
  );
  assert.throws(
    () =>
      assertLeaderTacticsV2PageContract(
        page({ production_mutation_allowed: true as never }),
        filters
      ),
    /research-only/
  );
  assert.throws(
    () =>
      assertLeaderTacticsV2PageContract(page({ universe: "ashare" }), filters),
    /universe boundary/
  );
  assert.throws(
    () =>
      assertLeaderTacticsV2PageContract(
        page({ gate_facts: { raw_price: 1 } } as Partial<CandidatesResponse>),
        filters
      ),
    /raw-price fallback/
  );
  const liveCandidate = candidate();
  liveCandidate.provenance.notification_provenance = "simulated";
  assert.throws(
    () =>
      assertLeaderTacticsV2PageContract(
        page({ candidates: [liveCandidate] }),
        filters
      ),
    /candidate has invalid provenance/
  );
  assert.match(
    leaderTacticsV2UnavailableText("leader_tactics_v2_not_materialized"),
    /物化/
  );
  assert.match(
    leaderTacticsV2UnavailableText("unknown_reason"),
    /unknown_reason/
  );
  assert.match(
    LEADER_TACTICS_V2_RESEARCH_COPY.disclaimer,
    /不会改变正式综合排名/
  );
  assert.match(
    LEADER_TACTICS_V2_RESEARCH_COPY.evidenceBoundary,
    /原始价格回填/
  );
});

test("a stale response from another filter is rejected instead of falling back", async () => {
  const controller = new AbortController();
  await assert.rejects(
    fetchLeaderTacticsV2Page(
      async () => ({
        data: page({ universe: "ashare", candidates: [candidate("ashare")] })
      }),
      filters,
      null,
      controller.signal
    ),
    /universe boundary/
  );
});

test("cancelled requests never accept a late response", async () => {
  const beforeRequest = new AbortController();
  beforeRequest.abort();
  let calls = 0;
  await assert.rejects(
    fetchLeaderTacticsV2Page(
      async () => {
        calls += 1;
        return { data: page() };
      },
      filters,
      null,
      beforeRequest.signal
    ),
    (error: unknown) =>
      error instanceof DOMException && error.name === "AbortError"
  );
  assert.equal(calls, 0);

  const duringRequest = new AbortController();
  let resolveRequest:
    | ((value: { data: CandidatesResponse }) => void)
    | undefined;
  const pending = new Promise<{ data: CandidatesResponse }>((resolve) => {
    resolveRequest = resolve;
  });
  const request = fetchLeaderTacticsV2Page(
    async () => pending,
    filters,
    null,
    duringRequest.signal
  );
  duringRequest.abort();
  resolveRequest?.({ data: page() });
  await assert.rejects(
    request,
    (error: unknown) =>
      error instanceof DOMException && error.name === "AbortError"
  );
});

test("post-close watchlists require explicit non-PIT timing provenance", () => {
  const postClose = page({
    ranking_source_kind: "post_close_watchlist",
    decision_mode: "post_close_watchlist",
    feature_trade_date: "2026-08-07",
    membership_evaluation_date: "2026-08-08",
    next_eligible_date: "2026-08-10",
    historical_validation_eligible: false
  });
  assert.equal(
    assertLeaderTacticsV2PageContract(postClose, filters),
    postClose
  );
  assert.throws(
    () =>
      assertLeaderTacticsV2PageContract(
        { ...postClose, next_eligible_date: null },
        filters
      ),
    /post-close timing is incomplete/
  );
});

test("turning watches remain non-actionable and universe isolated", () => {
  const watchFilters: LeaderTacticsV2Filters = {
    ...filters,
    universe: "ashare",
    state: "turning_watch"
  };
  const watch = {
    ...candidate("ashare"),
    state: "turning_watch" as const,
    qualifies: false,
    score: null,
    gate_facts: {
      turning_watch_missing_conditions: "core_leader",
      turning_watch_core_distance: 0.12
    }
  };
  const watchPage = page({
    universe: "ashare",
    state: "turning_watch",
    candidates: [watch]
  });
  assert.equal(
    assertLeaderTacticsV2PageContract(watchPage, watchFilters),
    watchPage
  );
  assert.throws(
    () =>
      assertLeaderTacticsV2PageContract(
        page({
          universe: "ashare",
          state: "turning_watch",
          candidates: [{ ...watch, qualifies: true, score: 0.8 }]
        }),
        watchFilters
      ),
    /non-actionable/
  );
});
