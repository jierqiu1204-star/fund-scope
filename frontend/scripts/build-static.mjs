import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(scriptDir, "..");
const nextBin = path.join(frontendDir, "node_modules", "next", "dist", "bin", "next");

const result = spawnSync(process.execPath, [nextBin, "build"], {
  cwd: frontendDir,
  env: {
    ...process.env,
    NEXT_STATIC_EXPORT: "1"
  },
  stdio: "inherit"
});

process.exit(result.status ?? 1);
