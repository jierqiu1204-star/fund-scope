import { readFileSync } from "node:fs";
import { join } from "node:path";

const page = readFileSync(join(process.cwd(), "app", "short-term", "page.tsx"), "utf8");
const types = readFileSync(join(process.cwd(), "lib", "types.ts"), "utf8");

const checks = [
  {
    name: "declares the cached point-in-time shadow contract",
    pass:
      types.includes("export type EtfCatalystShadow") &&
      types.includes("source_published_at: string") &&
      types.includes("first_received_at: string") &&
      types.includes('mapping_kind: "direct" | "proxy"') &&
      types.includes("catalyst_shadow?: EtfCatalystShadow")
  },
  {
    name: "renders every explicit coverage state",
    pass:
      ["active", "observed_none", "unavailable", "not_applicable"].every((state) =>
        page.includes(`case "${state}"`)
      )
  },
  {
    name: "shows direct facts, proxy labels, corrections, and official source links",
    pass:
      page.includes("直接映射") &&
      page.includes("代理映射（仅展示）") &&
      page.includes("更正版") &&
      page.includes("官方来源")
  },
  {
    name: "keeps the shadow visually and behaviorally isolated",
    pass:
      page.split("data-catalyst-shadow").length - 1 === 1 &&
      page.split("<CatalystShadowPanel").length - 1 >= 2 &&
      page.includes("影子研究，权重为 0") &&
      page.includes("不改变研究榜、可行动榜、组合配置、持仓动作或邮件状态")
  },
  {
    name: "retires fixed catalyst scoring displays",
    pass:
      !page.includes('StatPill label="催化分"') &&
      !page.includes('StatPill label="事件热度"') &&
      !page.includes("催化 15%") &&
      !page.includes("催化 20%")
  }
];

const failed = checks.filter((check) => !check.pass);
if (failed.length) {
  console.error("ETF catalyst shadow checks failed:");
  for (const check of failed) console.error(`- ${check.name}`);
  process.exit(1);
}

console.log("ETF catalyst shadow checks passed.");
