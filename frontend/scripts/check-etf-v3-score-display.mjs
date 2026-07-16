import { readFileSync } from "node:fs";
import { join } from "node:path";

const page = readFileSync(
  join(process.cwd(), "app", "short-term", "page.tsx"),
  "utf8"
);
const types = readFileSync(join(process.cwd(), "lib", "types.ts"), "utf8");

const v3Lookup = page.indexOf("score_breakdown?.final_score_v3");
const v2Lookup = page.indexOf("score_breakdown?.final_score_v2");

const checks = [
  {
    name: "declares the canonical v3 breakdown payload",
    pass: types.includes("final_score_v3?: ShortResearchFinalScoreBreakdown")
  },
  {
    name: "reads v3 before the legacy v2 breakdown",
    pass: v3Lookup >= 0 && v2Lookup > v3Lookup
  },
  {
    name: "labels only v3 as the current score contract",
    pass:
      page.includes('version === "final_score_v3"') &&
      page.includes('return "当前评分口径"') &&
      !page.includes('version === "final_score_v2" ? "新评分口径"') &&
      !page.includes('item.score_version === "final_score_v2"')
  },
  {
    name: "renders canonical v3 component scores",
    pass:
      page.includes("technical_momentum_cross_section") &&
      page.includes("risk_quality_cross_section") &&
      page.includes("component_scores")
  },
  {
    name: "keeps an unavailable live preview score null",
    pass:
      page.includes(
        "total_score: item.live_total_score ?? item.base_score ?? null"
      ) &&
      !page.includes(
        "total_score: item.live_total_score ?? item.base_score ?? 0"
      )
  },
  {
    name: "renders ETF score from eligible ranking_score without a total_score fallback",
    pass:
      page.includes("asset.score_eligible === true") &&
      page.includes("asset.ranking_score") &&
      page.includes("暂无分数") &&
      types.includes("total_score: number | null")
  },
  {
    name: "does not let ETF advisor requests fall back to a legacy signal run",
    pass:
      page.includes('assetType === "etf"') &&
      page.includes("shortAssetData?.snapshot?.snapshot_id ?? null") &&
      !page.includes(
        "etfLiveData?.snapshot?.snapshot_id ?? shortAssetData?.snapshot?.snapshot_id ?? latestCompletedSignal.data?.id"
      )
  },
  {
    name: "explains why score-bucket returns are unavailable instead of showing a context-free N/A",
    pass:
      page.includes("scoreBucketUnavailableText") &&
      page.includes('case "waiting_signal_generation"') &&
      page.includes('case "no_available_final_decision_score"') &&
      page.includes("不使用旧分数或原始价格补算")
  }
];

const failed = checks.filter((check) => !check.pass);
if (failed.length) {
  console.error("ETF v3 score display checks failed:");
  for (const check of failed) {
    console.error(`- ${check.name}`);
  }
  process.exit(1);
}

console.log("ETF v3 score display checks passed.");
