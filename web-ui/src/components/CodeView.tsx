import { Markdown } from "@/components/Markdown";

const EXT_LANG: Record<string, string> = {
  ts: "typescript",
  tsx: "tsx",
  js: "javascript",
  jsx: "jsx",
  py: "python",
  rb: "ruby",
  go: "go",
  rs: "rust",
  java: "java",
  kt: "kotlin",
  c: "c",
  h: "c",
  cpp: "cpp",
  cc: "cpp",
  hpp: "cpp",
  cs: "csharp",
  php: "php",
  swift: "swift",
  sh: "bash",
  bash: "bash",
  zsh: "bash",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  ini: "ini",
  md: "markdown",
  html: "html",
  css: "css",
  scss: "scss",
  sql: "sql",
  xml: "xml",
  dockerfile: "dockerfile",
};

/** Best-effort language tag for highlight.js from a file path/name. */
export function langFromPath(path: string | undefined): string {
  if (!path) return "";
  const base = path.split("/").pop() ?? path;
  if (base.toLowerCase() === "dockerfile") return "dockerfile";
  const ext = base.includes(".") ? base.split(".").pop()!.toLowerCase() : "";
  return EXT_LANG[ext] ?? "";
}

/**
 * Render file content as a syntax-highlighted code block. Reuses the
 * markdown/highlight.js pipeline; the fence length is widened past any
 * run of backticks in the content so embedded fences don't break out.
 */
export function CodeView({
  content,
  path,
  lang,
}: {
  content: string;
  path?: string;
  lang?: string;
}) {
  const language = lang ?? langFromPath(path);
  const longestTicks = (content.match(/`+/g) ?? []).reduce(
    (max, run) => Math.max(max, run.length),
    0,
  );
  const fence = "`".repeat(Math.max(3, longestTicks + 1));
  return <Markdown>{`${fence}${language}\n${content}\n${fence}`}</Markdown>;
}
