import { readFileSync } from "node:fs";
import { join } from "node:path";

const page = readFileSync(
  join(process.cwd(), "app", "short-term", "page.tsx"),
  "utf8"
);
const types = readFileSync(join(process.cwd(), "lib", "types.ts"), "utf8");
const etfSortOptions = page.match(/const etfSortOptions:[\s\S]*?\n\];/)?.[0] ?? "";

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
    name: "uses one ETF score sort without the duplicate opportunity alias",
    pass:
      etfSortOptions.includes('{ key: "score", label: "按综合分" }') &&
      !etfSortOptions.includes("opportunity") &&
      page.includes('useState<SortKey>("score")') &&
      page.includes('if (result.kind === "canonical_refresh") {\n        setSort("score");') &&
      page.includes('setAssetType(item);\n                  setSort("score");')
  },
  {
    name: "uses distinct daily research and intraday actionable surfaces",
    pass:
      page.includes('["research", "日线研究榜"]') &&
      page.includes('["actionable", "盘中可行动榜"]')
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
      page.includes("暂无可行动资格：缺少行动证据") &&
      page.includes("不会用研究榜、旧行情或估算数据补候选")
  },
  {
    name: "shows coverage and timestamps on both responsive layouts",
    pass:
      page.includes("shortAssetData?.snapshot?.decision_data_coverage_ratio") &&
      page.includes("shortAssetData?.snapshot?.score_coverage_ratio") &&
      page.includes("shortAssetData?.snapshot?.as_of_trade_date") &&
      page.split("研究榜与行动资格").length - 1 >= 2
  },
  {
    name: "renders existing provisional, stale, taxonomy, liquidity, and coverage states honestly",
    pass:
      types.includes(
        'coverage_policy_mode?: "blocked" | "degraded" | "complete" | null'
      ) &&
      types.includes(
        'snapshot_state?: "unavailable" | "provisional" | "complete"'
      ) &&
      page.includes('coverage_policy_mode === "blocked"') &&
      page.includes("临时研究预览：只展示日线研究榜") &&
      page.includes("快照已过期：等待同一合同下的新鲜日线数据后再判断") &&
      page.includes("主题归类未知：") &&
      page.includes("流动性风险：已触发“流动性不足”门槛") &&
      page.includes("盘中行动字段：") &&
      page.includes('snapshot_state === "provisional"')
  },
  {
    name: "keeps research, shadow, delivery, and execution evidence separate",
    pass:
      page.includes("影子研究，权重为 0；不改变研究榜、可行动榜、组合配置、持仓动作或邮件状态") &&
      page.includes("历史回放与真实前瞻均为研究证据，不等同于正式策略业绩、邮件送达或用户确认成交") &&
      page.includes("不等同于历史回放表现、邮件送达或用户确认成交")
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
