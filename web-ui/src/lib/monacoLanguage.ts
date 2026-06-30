/** Map a file path to a Monaco language id (best-effort; defaults to plaintext). */
const BY_EXT: Record<string, string> = {
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  mjs: "javascript",
  cjs: "javascript",
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
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "ini",
  ini: "ini",
  xml: "xml",
  html: "html",
  htm: "html",
  svg: "xml",
  css: "css",
  scss: "scss",
  less: "less",
  sql: "sql",
  sh: "shell",
  bash: "shell",
  zsh: "shell",
  dockerfile: "dockerfile",
  md: "markdown",
  markdown: "markdown",
  mdx: "markdown",
};

export function monacoLanguageFromPath(path: string): string {
  const name = (path.split("/").pop() ?? "").toLowerCase();
  if (name === "dockerfile") return "dockerfile";
  if (name === "makefile") return "makefile";
  const dot = name.lastIndexOf(".");
  const ext = dot >= 0 ? name.slice(dot + 1) : "";
  return BY_EXT[ext] ?? "plaintext";
}

export function isMarkdownPath(path: string): boolean {
  const ext = (path.split(".").pop() ?? "").toLowerCase();
  return ext === "md" || ext === "markdown" || ext === "mdx";
}
