import { readFileSync } from "node:fs";
import { join } from "node:path";

const panel = readFileSync(
  join(process.cwd(), "components", "leader-tactics-v2-panel.tsx"),
  "utf8"
);
const contract = readFileSync(
  join(process.cwd(), "lib", "leader-tactics-v2-contract.ts"),
  "utf8"
);
const layout = readFileSync(join(process.cwd(), "app", "layout.tsx"), "utf8");
const page = readFileSync(
  join(process.cwd(), "app", "short-term", "leader-tactics", "page.tsx"),
  "utf8"
);

const checks = [
  {
    name: "contains no accidentally escaped template literals",
    pass: !panel.includes("\\`") && !panel.includes("\\${")
  },
  {
    name: "uses a real infinite query with a stable page parameter",
    pass:
      panel.includes("useInfiniteQuery") &&
      panel.includes("initialPageParam") &&
      panel.includes("getNextPageParam") &&
      panel.includes("fetchNextPage") &&
      panel.includes("fetchLeaderTacticsV2Page")
  },
  {
    name: "uses the shared filter key and page contract",
    pass:
      panel.includes("leaderTacticsV2QueryKey(filters)") &&
      panel.includes("mergeLeaderTacticsV2Pages(pages)")
  },
  {
    name: "defaults to the materialized A-share research universe",
    pass: panel.includes('useState<Universe>("ashare")')
  },
  {
    name: "renders qualification and availability evidence",
    pass:
      panel.includes("candidate.availability") &&
      panel.includes("candidate.qualifies") &&
      panel.includes("summary.exclusion_counts") &&
      panel.includes("leaderTacticsV2UnavailableText")
  },
  {
    name: "renders research and notification/execution provenance",
    pass:
      panel.includes("manifest_hash") &&
      panel.includes("notification_provenance") &&
      panel.includes("execution_provenance") &&
      panel.includes("LEADER_TACTICS_V2_RESEARCH_COPY")
  },
  {
    name: "keeps the research surface isolated from production actions",
    pass:
      contract.includes("不会改变正式综合排名、持仓、邮件或执行") &&
      contract.includes("production_mutation_allowed") &&
      panel.includes("LEADER_TACTICS_V2_RESEARCH_COPY")
  },
  {
    name: "does not implement a cross-universe or raw-price fallback",
    pass:
      contract.includes("不从另一标的池或原始价格回填") &&
      contract.includes("raw-price fallback") &&
      !panel.includes("fallbackUniverse") &&
      !panel.includes("raw_price")
  },
  {
    name: "keeps navigation and direct access disabled by default",
    pass:
      layout.includes("NEXT_PUBLIC_ETF_LEADER_TACTICS_V2_ENABLED") &&
      page.includes("NEXT_PUBLIC_ETF_LEADER_TACTICS_V2_ENABLED") &&
      page.includes("暂未启用")
  }
];

const failed = checks.filter((check) => !check.pass);
if (failed.length) {
  console.error("Leader-tactics V2 frontend checks failed:");
  for (const check of failed) console.error(`- ${check.name}`);
  process.exit(1);
}

console.log("Leader-tactics V2 frontend checks passed.");
