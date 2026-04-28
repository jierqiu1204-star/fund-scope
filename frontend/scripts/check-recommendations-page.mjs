import { readFileSync } from "node:fs";
import { join } from "node:path";

const pagePath = join(process.cwd(), "app", "recommendations", "page.tsx");
const content = readFileSync(pagePath, "utf8");

for (const required of ["Fund candidates", "Stock watchlist", "safe_label", "risk_flags"]) {
  if (!content.includes(required)) {
    throw new Error(`recommendations page is missing ${required}`);
  }
}

for (const forbidden of ["买入", "卖出", "目标价", "target price", "expected return"]) {
  if (content.toLowerCase().includes(forbidden.toLowerCase())) {
    throw new Error(`recommendations page contains prohibited wording: ${forbidden}`);
  }
}
