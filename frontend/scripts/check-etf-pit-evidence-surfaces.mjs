import { readFileSync } from "node:fs";
import { join } from "node:path";

const page = readFileSync(
  join(process.cwd(), "app", "short-term", "evidence", "page.tsx"),
  "utf8"
);
const types = readFileSync(join(process.cwd(), "lib", "types.ts"), "utf8");

const separateSurfaces = [
  "production_ranking",
  "research_replay",
  "policy_shadow",
  "live_notification",
  "provider_delivery",
  "confirmed_execution"
];
const labels = [
  "正式 V3 榜单",
  "历史 research replay",
  "Policy shadow",
  "真实邮件结果",
  "服务商送达",
  "用户确认成交"
];

const checks = [
  {
    name: "declares all provenance-separated evidence surfaces",
    pass: separateSurfaces.every(
      (surface) => types.includes(surface) && page.includes(surface)
    )
  },
  {
    name: "renders six evidence sections without merging simulation and live facts",
    pass:
      labels.every((label) => page.includes(label)) &&
      page.includes("模拟结果不会升级为生产业绩")
  },
  {
    name: "renders stable unavailable categories without fallback metrics",
    pass: ["样本不足", "覆盖不足", "等待未来窗口", "版本不一致"].every(
      (label) => page.includes(label)
    )
  },
  {
    name: "shows notification and execution provenance independently",
    pass:
      page.includes("通知 provenance") &&
      page.includes("执行 provenance") &&
      types.includes("provider_receipt")
  },
  {
    name: "uses the additive read-only evidence endpoint",
    pass: page.includes("/api/short-research/evidence/etf/latest")
  }
];

const failed = checks.filter((check) => !check.pass);
if (failed.length) {
  console.error("ETF PIT evidence surface checks failed:");
  for (const check of failed) {
    console.error(`- ${check.name}`);
  }
  process.exit(1);
}

console.log("ETF PIT evidence surface checks passed.");
