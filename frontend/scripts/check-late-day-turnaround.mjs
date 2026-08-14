import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const read = (value) => fs.readFileSync(path.join(root, value), "utf8");

const panel = read("components/late-day-turnaround-panel.tsx");
const trackingPage = read("app/short-term/page.tsx");
const types = read("lib/types.ts");
const page = read("app/short-term/late-day-turnaround/page.tsx");
const layout = read("app/layout.tsx");
const contract = read("lib/late-day-turnaround-contract.ts");

const checks = [
  panel.includes("/api/short-research/late-day-turnaround/candidates"),
  panel.includes('option value="etf"'),
  panel.includes('option value="ashare"'),
  panel.includes('option value="formal_candidate"'),
  panel.includes('option value="daily_proxy_watchlist"'),
  panel.includes("不会改变 ETF"),
  panel.includes("notification") === false,
  page.includes("NEXT_PUBLIC_LATE_DAY_TURNAROUND_ENABLED"),
  layout.includes("NEXT_PUBLIC_LATE_DAY_TURNAROUND_ENABLED"),
  contract.includes('notification_provenance: "none"'),
  contract.includes('execution_provenance: "none"'),
  trackingPage.includes("TrackingAlertPolicyId"),
  trackingPage.includes("邮件提醒规则"),
  trackingPage.includes("默认动态止盈止损（默认）"),
  trackingPage.includes("尾盘转强 T+1"),
  trackingPage.includes("alert_policy_id:"),
  trackingPage.includes("最迟 10:30 全部退出"),
  types.includes('"leader_tactics_exit_v1"'),
  types.includes("TrackingAlertPolicyProvenance")
];

if (checks.some((value) => !value)) {
  throw new Error("late-day turnaround research boundary check failed");
}

console.log("late-day turnaround research surface checks passed");
