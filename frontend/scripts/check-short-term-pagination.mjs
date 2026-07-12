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
  },
  {
    name: "clamps offset when result totals shrink",
    pass: source.includes("lastValidOffset") && source.includes("setAssetOffset(lastValidOffset)")
  },
  {
    name: "labels pagination count scope",
    pass: source.includes("scopeLabel={assetCountScope}") && source.includes("当前页 {start} - {end}")
  },
  {
    name: "loads health detail for the issue panel",
    pass: source.includes("/api/short-research/status?include_health=true")
  },
  {
    name: "keys private tracking data by stable user identity",
    pass: source.includes('["tracked-positions", user?.id ?? "anonymous"]')
  },
  {
    name: "uses server polling intervals and next boundary hints",
    pass: source.includes("data.next_poll_seconds") && source.includes("data.page_poll_seconds")
  },
  {
    name: "passes React Query abort signals to list requests",
    pass: source.includes("api.get<ShortResearchAssetList>") && source.includes("{ signal }")
  },
  {
    name: "uses Shanghai calendar values for editable dates",
    pass: source.includes("shanghaiClockParts") && source.includes("timeZone: \"Asia/Shanghai\"")
  },
  {
    name: "prevents overlapping research actions",
    pass: source.includes("const isResearchTaskPending") && source.includes("source_signal_run_id: advisorSourceRunId")
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
