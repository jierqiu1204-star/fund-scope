import { readFileSync } from "node:fs";
import { join } from "node:path";

const page = readFileSync(join(process.cwd(), "app", "short-term", "evidence", "page.tsx"), "utf8");
const types = readFileSync(join(process.cwd(), "lib", "types.ts"), "utf8");

const checks = [
  {
    name: "declares separate result dimensions",
    pass: [
      "action_evidence",
      "notification_evidence",
      "execution_evidence",
      "coverage_evidence",
      "time_resolution_limitations"
    ].every((field) => types.includes(field))
  },
  {
    name: "renders action and SMTP sensitivity as different research scenarios",
    pass:
      page.includes("动作建议完全执行情景") &&
      page.includes("仅 SMTP 已接受邮件被执行敏感性") &&
      page.includes("SMTP 接受不等于送达或执行")
  },
  {
    name: "renders execution, coverage, and time-resolution limits separately",
    pass: ["执行模型", "数据覆盖", "时间粒度限制"].every((label) => page.includes(label))
  },
  {
    name: "does not relabel the legacy daily model as adjusted-open execution",
    pass:
      page.includes("legacy_daily_close") &&
      page.includes("旧日线收盘口径") &&
      page.includes("adjusted_open") &&
      page.includes("次日复权开盘")
  },
  {
    name: "discloses missing intraday market and real-execution proof",
    pass:
      page.includes("bid/ask") &&
      page.includes("IOPV") &&
      page.includes("用户真实执行")
  }
];

const failed = checks.filter((check) => !check.pass);
if (failed.length) {
  console.error("ETF backtest evidence dimension checks failed:");
  for (const check of failed) {
    console.error(`- ${check.name}`);
  }
  process.exit(1);
}

console.log("ETF backtest evidence dimension checks passed.");
