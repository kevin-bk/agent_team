// Copy the agent-team build output into the plugin's static dir.
//
// This web-ui lives inside the agent_team plugin (`agent_team/web-ui`); the
// plugin serves the built bundle from `agent_team/static`. This script runs from
// `npm run build:agent-team` after Vite produces `dist-agent-team/`.

import { existsSync, rmSync, mkdirSync, cpSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const distDir = resolve(here, "..", "dist-agent-team");
// here = agent_team/web-ui/scripts -> ../../static = agent_team/static
const targetDir = resolve(here, "..", "..", "static");

if (!existsSync(distDir)) {
  console.error(`[copy-to-plugin] build output not found: ${distDir}`);
  process.exit(1);
}

rmSync(targetDir, { recursive: true, force: true });
mkdirSync(targetDir, { recursive: true });
cpSync(distDir, targetDir, { recursive: true });

console.log(`[copy-to-plugin] copied ${distDir} -> ${targetDir}`);
