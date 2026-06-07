import { readFileSync } from "node:fs";
import { join } from "node:path";

const redirectPath = join(process.cwd(), "app", "recommendations", "page.tsx");
const labPath = join(process.cwd(), "app", "strategy-lab", "page.tsx");
const redirectContent = readFileSync(redirectPath, "utf8");
const content = readFileSync(labPath, "utf8");

if (!redirectContent.includes("/strategy-lab?view=screening")) {
  throw new Error("recommendations page should redirect to the strategy lab screening view");
}

for (const required of ["筛选评分", "safe_label", "risk_flags", "策略实验室"]) {
  if (!content.includes(required)) {
    throw new Error(`strategy lab screening page is missing ${required}`);
  }
}

for (const forbidden of ["买入建议", "卖出建议", "目标价", "target price", "expected return"]) {
  if (content.toLowerCase().includes(forbidden.toLowerCase())) {
    throw new Error(`recommendations page contains prohibited wording: ${forbidden}`);
  }
}
