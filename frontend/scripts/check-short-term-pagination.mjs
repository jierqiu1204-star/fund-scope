import { readFileSync } from "node:fs";
import { join } from "node:path";

const source = readFileSync(join(process.cwd(), "app", "short-term", "page.tsx"), "utf8");

const checks = [
  {
    name: "uses a 12-item page size constant",
    pass: source.includes("const ASSET_PAGE_SIZE = 12;")
  },
  {
    name: "passes page size constant to the assets API",
    pass: source.includes('params.set("limit", String(ASSET_PAGE_SIZE));')
  },
  {
    name: "previous page subtracts page size constant",
    pass: source.includes("Math.max(0, value - ASSET_PAGE_SIZE)")
  },
  {
    name: "next page adds page size constant",
    pass: source.includes("value + ASSET_PAGE_SIZE")
  },
  {
    name: "renders pagination controls above and below the list",
    pass: (source.match(/<AssetPaginationBar/g) ?? []).length >= 2
  }
];

const failed = checks.filter((check) => !check.pass);
if (failed.length) {
  console.error("Short-term pagination checks failed:");
  for (const check of failed) {
    console.error(`- ${check.name}`);
  }
  process.exit(1);
}

console.log("Short-term pagination checks passed.");
