import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const frontendDir = path.resolve(scriptDir, "..");
const nodeBin = process.execPath;
const nextBin = path.join(frontendDir, "node_modules", "next", "dist", "bin", "next");
const tscBin = path.join(frontendDir, "node_modules", "typescript", "bin", "tsc");

const commands = [
  [nextBin, "lint"],
  [tscBin, "--noEmit"]
];

for (const [command, ...args] of commands) {
  const result = spawnSync(nodeBin, [command, ...args], {
    cwd: frontendDir,
    env: process.env,
    stdio: "inherit"
  });

  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}
