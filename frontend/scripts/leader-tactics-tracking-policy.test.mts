import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";

import {
  buildLeaderCandidateTrackingPayload,
  leaderCandidateTrackingProvenance,
  leaderTacticsTrackingErrorText,
  type Candidate,
  type CandidatesResponse
} from "../lib/leader-tactics-v2-contract.ts";

const root = process.cwd();

function candidate(overrides: Partial<Candidate> = {}): Candidate {
  return {
    manifest_hash: "manifest-leader-1",
    universe: "ashare",
    asset_code: "600000",
    asset_name: "示例个股",
    theme: "AI 应用",
    sector: "计算机",
    tracked_index: null,
    formula_id: "leader_breakout_proxy_v2",
    state: "confirmed",
    availability: "available",
    qualifies: true,
    score: 0.9,
    signal_date: "2026-08-13",
    transition_date: "2026-08-14",
    source_cutoff: "2026-08-14T15:00:00",
    gate_facts: {},
    exclusion_reasons: [],
    provenance: {
      research_only: true,
      notification_provenance: "none",
      execution_provenance: "none"
    },
    feature_hash: "feature-1",
    sentiment_risk_state: "healthy",
    sentiment_risk_action_mode: "shadow_entry_allowed",
    sentiment_risk_new_entry_allowed: true,
    sentiment_risk_provenance: {},
    ...overrides
  };
}

const response = {
  manifest_hash: "manifest-leader-1",
  manifest_decision_cutoff: "2026-08-14T15:00:00"
} as Pick<CandidatesResponse, "manifest_hash" | "manifest_decision_cutoff">;

const values = {
  buyDate: "2026-08-14",
  buyAmount: 3000,
  orderTimeBucket: "before_15" as const,
  confirmedNavDate: "2026-08-14",
  confirmedNav: 12.34,
  confirmedShares: 243,
  note: "用户手动成交"
};

test("confirmed A-share candidate creates stock leader payload with provenance", () => {
  const payload = buildLeaderCandidateTrackingPayload(
    candidate(),
    response.manifest_hash,
    response.manifest_decision_cutoff,
    values
  );

  assert.deepEqual(payload, {
    asset_type: "stock",
    asset_code: "600000",
    buy_amount: 3000,
    buy_date: "2026-08-14",
    order_time_bucket: "before_15",
    confirmed_nav_date: "2026-08-14",
    confirmed_nav: 12.34,
    confirmed_shares: 243,
    note: "用户手动成交",
    alert_policy_id: "leader_tactics_exit_v1",
    source_manifest_hash: "manifest-leader-1",
    source_decision_at: "2026-08-14T15:00:00+08:00"
  });
});

test("ETF leader payload keeps the ETF universe and policy isolated from ranking", () => {
  const payload = buildLeaderCandidateTrackingPayload(
    candidate({
      universe: "etf",
      asset_code: "512710",
      asset_name: "军工 ETF"
    }),
    response.manifest_hash,
    response.manifest_decision_cutoff,
    values
  );

  assert.equal(payload.asset_type, "etf");
  assert.equal(payload.alert_policy_id, "leader_tactics_exit_v1");
  assert.equal("ranking_score" in payload, false);
  assert.equal("notification_provenance" in payload, false);
});

test("unconfirmed or incomplete candidates are manual and never receive source fields", () => {
  const preparing = leaderCandidateTrackingProvenance(
    candidate({ state: "preparing" }),
    response.manifest_hash,
    response.manifest_decision_cutoff
  );
  const incomplete = leaderCandidateTrackingProvenance(
    candidate({ manifest_hash: "" }),
    null,
    null
  );
  assert.throws(
    () =>
      buildLeaderCandidateTrackingPayload(
        candidate({ state: "turning_watch" }),
        response.manifest_hash,
        response.manifest_decision_cutoff,
        values
      ),
    /明确选择不绑定研究来源/
  );
  const payload = buildLeaderCandidateTrackingPayload(
    candidate({ state: "turning_watch" }),
    response.manifest_hash,
    response.manifest_decision_cutoff,
    values,
    "manual_selection"
  );
  const forcedManual = buildLeaderCandidateTrackingPayload(
    candidate(),
    response.manifest_hash,
    response.manifest_decision_cutoff,
    values,
    "manual_selection"
  );

  assert.equal(preparing.kind, "manual_selection");
  assert.equal(preparing.reason, "not_confirmed");
  assert.equal(incomplete.kind, "manual_selection");
  assert.equal(incomplete.reason, "missing_manifest_hash");
  assert.equal("source_manifest_hash" in payload, false);
  assert.equal("source_decision_at" in payload, false);
  assert.equal("source_manifest_hash" in forcedManual, false);
  assert.equal("source_decision_at" in forcedManual, false);
});

test("frontend keeps leader entry research-only and limits policy selector to ETF", () => {
  const trackingPage = fs.readFileSync(
    path.join(root, "app/short-term/page.tsx"),
    "utf8"
  );
  const panel = fs.readFileSync(
    path.join(root, "components/leader-tactics-v2-panel.tsx"),
    "utf8"
  );

  assert.match(trackingPage, /assetType === "etf"/);
  assert.match(trackingPage, /leader_tactics_exit_v1/);
  assert.match(panel, /我已买入，开始追踪/);
  assert.match(panel, /不发送候选或买入邮件/);
  assert.match(panel, /不绑定研究来源，按手动选择/);
  assert.match(panel, /切换为手动选择后重试/);
  assert.match(panel, /sourceMode/);
  assert.match(panel, /api\.post\("\/api\/tracked-positions"/);
  assert.match(panel, /createTracking\.mutate\(\)/);
  assert.match(
    panel,
    /invalidateQueries\(\{ queryKey: \["tracked-positions"\] \}\)/
  );
  assert.match(panel, /production_mutation_allowed/);
});

test("API and evidence failures use stable Chinese messages", () => {
  assert.match(
    leaderTacticsTrackingErrorText({
      response: { data: { detail: "leader policy 只支持 ETF 或 stock" } }
    }),
    /当前标的类型不能使用/
  );
  assert.match(
    leaderTacticsTrackingErrorText({
      response: {
        data: {
          detail: "source_manifest_hash 和 source_decision_at 必须同时提供"
        }
      }
    }),
    /候选来源校验失败.*手动选择.*不会自动降级/
  );
  assert.match(
    leaderTacticsTrackingErrorText(new Error("network")),
    /创建龙头战法追踪失败/
  );
});
