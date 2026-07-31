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
