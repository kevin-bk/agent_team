/**
 * Rank workspace paths by "importance" so the Files browser can surface
 * entrypoints (`README.md`, `package.json`, `index.ts`, …) as quick-access
 * pills ahead of nested utility modules. Kept dependency-free so it's trivial
 * to test.
 */

/** Basenames (lowercased) that are almost always a project entrypoint. */
const HIGH_PRIORITY_BASENAMES: string[] = [
  "index.html",
  "index.htm",
  "readme.md",
  "readme",
  "main.html",
  "app.html",
  "index.js",
  "index.ts",
  "index.tsx",
  "index.jsx",
  "main.py",
  "app.py",
  "main.go",
  "main.rs",
  "main.java",
  "main.c",
  "main.cpp",
  "package.json",
  "pyproject.toml",
  "cargo.toml",
  "go.mod",
  "pom.xml",
  "dockerfile",
  "makefile",
];

/** Useful, but typically secondary to the entrypoints above. */
const SECONDARY_BASENAMES: string[] = [
  "license",
  "license.md",
  "license.txt",
  "changelog.md",
  "agents.md",
  "tsconfig.json",
  ".env.sample",
  ".env.example",
];

function getBasename(path: string): string {
  const idx = path.lastIndexOf("/");
  return (idx === -1 ? path : path.slice(idx + 1)).toLowerCase();
}

function pathDepth(path: string): number {
  return path.split("/").filter(Boolean).length - 1;
}

/**
 * Rank a path *within its own depth bucket*: high-priority entrypoints first
 * (in listed order), then secondary files, then everything else.
 */
export function filePriorityScore(path: string): number {
  const base = getBasename(path);
  const highIdx = HIGH_PRIORITY_BASENAMES.indexOf(base);
  if (highIdx !== -1) return highIdx;
  const secondaryIdx = SECONDARY_BASENAMES.indexOf(base);
  if (secondaryIdx !== -1) return 1000 + secondaryIdx;
  return 10000;
}

/**
 * Sort so the most likely "landing files" come first: shallower paths beat
 * deeper ones, ties broken by basename importance then alphabetically.
 */
export function sortFilesByPriority(paths: string[]): string[] {
  return [...paths].sort((a, b) => {
    const depthDiff = pathDepth(a) - pathDepth(b);
    if (depthDiff !== 0) return depthDiff;
    const scoreDiff = filePriorityScore(a) - filePriorityScore(b);
    if (scoreDiff !== 0) return scoreDiff;
    return a.localeCompare(b);
  });
}
