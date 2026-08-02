import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = process.cwd();
const evidencePage = readFileSync(
  join(root, "app", "short-term", "evidence", "page.tsx"),
  "utf8"
);
const productionPage = readFileSync(
  join(root, "app", "short-term", "page.tsx"),
  "utf8"
);
const types = readFileSync(join(root, "lib", "types.ts"), "utf8");

const candidateIds = [
  "leader_breakout_proxy_v1",
  "former_leader_repair_proxy_v1",
  "cycle_routed_leader_proxy_v1"
];

const checks = [
  {
    name: "uses a dedicated read-only leader evidence contract",
    pass:
      evidencePage.includes(
        "/api/short-research/evidence/etf/leader-tactics/latest"
      ) && types.includes("EtfLeaderTacticsEvidence")
  },
  {
    name: "labels the panel as separate research-only proxy evidence",
    pass:
      evidencePage.includes("龙头战术透明代理（研究）") &&
      evidencePage.includes('data-evidence-family="leader-tactics-shadow"') &&
      evidencePage.includes('data-research-only="true"')
  },
  {
    name: "states source non-equivalence and blocks endorsement or guarantee claims",
    pass:
      evidencePage.includes("不等于原作者专有信号") &&
      evidencePage.includes("不代表作者背书、正式榜单或收益保证")
  },
  {
    name: "separates primary endpoint from exploratory MA5 policy evidence",
    pass:
      evidencePage.includes("主指标：Top10 / 5 日配对净超额") &&
      evidencePage.includes("辅助诊断（不得替代主指标）") &&
      evidencePage.includes("MA5 policy shadow")
  },
  {
    name: "shows source, formula, coverage, exclusions, diagnostics and holdout",
    pass: [
      "来源与非等价说明",
      "冻结代理公式",
      "覆盖",
      "排除项",
      "残差重叠",
      "集中度",
      "市场状态稳定性",
      "Holdout"
    ].every((label) => evidencePage.includes(label))
  },
  {
    name: "keeps ranking, policy, notification and execution provenance independent",
    pass: [
      "ranking_source_kind",
      "policy_mode",
      "notification_provenance",
      "execution_provenance"
    ].every((field) => types.includes(field))
  },
  {
    name: "declares a bounded research-observation contract",
    pass: [
      "EtfLeaderObservationState",
      "EtfLeaderObservationCounts",
      "EtfLeaderCurrentObservation",
      "EtfLeaderPendingOutcome",
      "observation_state?:",
      "observation_unavailable_reason?:",
      "observation_data_cutoff?:",
      "observation_manifest_hash?:",
      "observation_counts?:",
      "current_observations?:",
      "pending_outcomes?:",
      "partial_checkpoint?:"
    ].every((field) => types.includes(field))
  },
  {
    name: "renders daily shadow observations, pending outcomes and exact progress without a trade claim",
    pass: [
      "当日 Shadow 观察",
      "待结算结果",
      "积累进度",
      "非买入信号、不会发邮件",
      "const currentObservations = (",
      "evidence.current_observations ?? []",
      "pendingOutcomes = (evidence.pending_outcomes ?? []).slice(0, 20)",
      "current_observations_truncated",
      "outcomes_by_horizon"
    ].every((field) => evidencePage.includes(field))
  },
  {
    name: "separates current-vintage historical proxy screening from factual PIT promotion",
    pass:
      evidencePage.includes("历史成员代理筛选（研究）") &&
      evidencePage.includes(
        'data-evidence-mode="source-snapshot-historical-proxy"'
      ) &&
      evidencePage.includes('data-pit-promotion-credit="0"') &&
      evidencePage.includes("它不是事实") &&
      evidencePage.includes("PIT 回放，不能计入") &&
      evidencePage.includes("也不是买入信号") &&
      types.includes("EtfLeaderHistoricalProxy") &&
      types.includes("promotion_gate_credit")
  },
  {
    name: "does not inject leader candidates into the production ranking page",
    pass:
      !productionPage.includes("龙头战术透明代理") &&
      !productionPage.includes("leader-tactics/latest") &&
      candidateIds.every((candidateId) => !productionPage.includes(candidateId))
  }
];

const failed = checks.filter((check) => !check.pass);
if (failed.length) {
  console.error("ETF leader-tactics shadow checks failed:");
  for (const check of failed) {
    console.error(`- ${check.name}`);
  }
  process.exit(1);
}

console.log("ETF leader-tactics shadow checks passed.");
