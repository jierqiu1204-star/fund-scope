import { readFileSync } from "node:fs";
import { join } from "node:path";

const page = readFileSync(
  join(process.cwd(), "app", "short-term", "page.tsx"),
  "utf8"
);
const types = readFileSync(join(process.cwd(), "lib", "types.ts"), "utf8");

const checks = [
  {
    name: "declares additive research and actionable contract fields",
    pass:
      types.includes('ranking_surface?: "research" | "actionable" | null') &&
      types.includes("research_rank?: number | null") &&
      types.includes("actionable_rank?: number | null") &&
      types.includes("history_confidence_tier?: string | null") &&
      types.includes("actionable_exclusion_reasons?: string[]") &&
      types.includes("actionable_source_times?: Record<string, string>")
  },
  {
    name: "defaults ETF discovery to the research surface",
    pass:
      page.includes('useState<RankingSurface>("research")') &&
      page.includes('params.set("ranking_surface", rankingSurface)')
  },
  {
    name: "defaults ETF sorting to the comprehensive ranking",
    pass:
      page.includes('{ key: "opportunity", label: "综合榜单" }') &&
      page.includes('useState<SortKey>("opportunity")') &&
      page.includes('setSort(item === "etf" ? "opportunity" : "score")')
  },
  {
    name: "offers a separate actionable filter",
    pass:
      page.includes('["research", "研究榜（默认）"]') &&
      page.includes('["actionable", "可行动榜"]')
  },
  {
    name: "keeps ranks and scores visibly separate",
    pass:
      page.includes("研究榜 #{item.research_rank") &&
      page.includes("可行动榜 #${asset.actionable_rank}") &&
      page.includes("研究榜与行动资格")
  },
  {
    name: "shows history tier and fail-closed action evidence",
    pass:
      page.includes("provisional_short_history") &&
      page.includes("暂不可行动：缺少行动证据") &&
      page.includes("不会用研究榜、旧行情或估算数据补候选")
  },
  {
    name: "shows coverage and timestamps on both responsive layouts",
    pass:
      page.includes("shortAssetData?.snapshot?.coverage_ratio") &&
      page.includes("shortAssetData?.snapshot?.as_of_trade_date") &&
      page.split("研究榜与行动资格").length - 1 >= 2
  },
  {
    name: "labels ninety-to-ninety-five percent research coverage as degraded",
    pass:
      types.includes(
        'coverage_policy_mode?: "blocked" | "degraded" | "complete" | null'
      ) &&
      page.includes('coverage_policy_mode === "degraded"') &&
      page.includes("当前为降级覆盖发布") &&
      page.includes("历史不足的 ETF 已排除")
  }
];

const failed = checks.filter((check) => !check.pass);
if (failed.length) {
  console.error("ETF dual ranking surface checks failed:");
  for (const check of failed) {
    console.error(`- ${check.name}`);
  }
  process.exit(1);
}

console.log("ETF dual ranking surface checks passed.");
