export interface GuideDocument {
  filename: string;
  slug: string;
  title: string;
  summary: string;
  section: string;
  content: string;
}

export interface GuideHeading {
  depth: 2 | 3;
  id: string;
  text: string;
}

interface GuideDefinition {
  filename: string;
  slug: string;
  title: string;
  summary: string;
  section: string;
}

const definitions: GuideDefinition[] = [
  {
    filename: "README.md",
    slug: "start",
    title: "Bắt đầu",
    summary: "Chọn lộ trình đọc phù hợp với vai trò và mục tiêu của bạn.",
    section: "Bắt đầu",
  },
  {
    filename: "02-agent-team-in-plain-language.md",
    slug: "agent-team-overview",
    title: "Agent Team là gì?",
    summary: "Bức tranh tổng quan bằng ngôn ngữ dễ hiểu.",
    section: "Bắt đầu",
  },
  {
    filename: "11-project-harness.md",
    slug: "project-harness",
    title: "project-harness",
    summary: "Phân biệt repository, skill và vai trò của planning skill.",
    section: "Hiểu hệ thống",
  },
  {
    filename: "12-acp-and-opensandbox.md",
    slug: "acp-and-opensandbox",
    title: "ACP và OpenSandbox",
    summary: "Cách Agent Team nói chuyện với coding agent và cô lập task.",
    section: "Hiểu hệ thống",
  },
  {
    filename: "13-engineering-loop.md",
    slug: "engineering-loop",
    title: "Engineering loop",
    summary: "Vòng lặp plan, code, test, đánh giá và sửa tiếp.",
    section: "Hiểu hệ thống",
  },
  {
    filename: "glossary.md",
    slug: "glossary",
    title: "Bảng thuật ngữ",
    summary: "Tra nhanh ACP, MCP, receipt, journal, verdict và các khái niệm khác.",
    section: "Hiểu hệ thống",
  },
  {
    filename: "01-before-the-first-task.md",
    slug: "before-first-task",
    title: "Trước task đầu tiên",
    summary: "Chuẩn bị Claude, Codex, skill packs và các quyền cần thiết.",
    section: "Cài đặt",
  },
  {
    filename: "03-administrator-setup.md",
    slug: "administrator-setup",
    title: "Thiết lập quản trị",
    summary: "Cấu hình repository, agent, skill và board ở Agent Manager.",
    section: "Cài đặt",
  },
  {
    filename: "08-runtime-and-sandbox.md",
    slug: "runtime-and-sandbox",
    title: "Runtime và sandbox",
    summary: "Chọn nơi chạy task và hiểu vòng đời workspace.",
    section: "Cài đặt",
  },
  {
    filename: "14-notification-channels.md",
    slug: "notification-channels",
    title: "Notification channels",
    summary: "Kết nối Mattermost hoặc Slack và chọn sự kiện cần gửi.",
    section: "Cài đặt",
  },
  {
    filename: "04-create-your-first-board.md",
    slug: "create-first-board",
    title: "Tạo board đầu tiên",
    summary: "Tạo không gian quản lý task cho một sản phẩm hoặc team.",
    section: "Sử dụng hằng ngày",
  },
  {
    filename: "05-run-your-first-task.md",
    slug: "run-first-task",
    title: "Chạy task đầu tiên",
    summary: "Từ yêu cầu ban đầu đến khi agent bắt đầu thực thi.",
    section: "Sử dụng hằng ngày",
  },
  {
    filename: "06-planning-and-approval.md",
    slug: "planning-and-approval",
    title: "Plan và phê duyệt",
    summary: "Đọc plan, đặt câu hỏi và quyết định cho agent tiếp tục.",
    section: "Sử dụng hằng ngày",
  },
  {
    filename: "07-testing-and-verification.md",
    slug: "testing-and-verification",
    title: "Test và verify",
    summary: "Hiểu command receipt, evidence, E2E và evaluator verdict.",
    section: "Sử dụng hằng ngày",
  },
  {
    filename: "09-chizy-end-to-end-example.md",
    slug: "chizy-example",
    title: "Ví dụ Chizy end-to-end",
    summary: "Một task Chizy đi qua toàn bộ engineering loop như thế nào.",
    section: "Ví dụ và hỗ trợ",
  },
  {
    filename: "10-troubleshooting.md",
    slug: "troubleshooting",
    title: "Xử lý sự cố",
    summary: "Chẩn đoán các lỗi thường gặp khi setup và chạy task.",
    section: "Ví dụ và hỗ trợ",
  },
];

const rawModules = import.meta.glob("../../../../user-guide/*.md", {
  eager: true,
  query: "?raw",
  import: "default",
}) as Record<string, string>;

const imageModules = import.meta.glob(
  "../../../../user-guide/assets/screenshots/*",
  {
    eager: true,
    query: "?url",
    import: "default",
  },
) as Record<string, string>;

const rawByFilename = new Map(
  Object.entries(rawModules).map(([path, content]) => [
    path.split("/").pop() ?? path,
    content,
  ]),
);

export const guideDocuments: GuideDocument[] = definitions.map((definition) => {
  const content = rawByFilename.get(definition.filename);
  if (!content) {
    throw new Error(`Missing bundled guide document: ${definition.filename}`);
  }
  return { ...definition, content };
});

export const guideSections = [...new Set(guideDocuments.map((doc) => doc.section))];

const bySlug = new Map(guideDocuments.map((doc) => [doc.slug, doc]));
const slugByFilename = new Map(
  guideDocuments.map((doc) => [doc.filename.toLowerCase(), doc.slug]),
);
const imagesByPath = new Map(
  Object.entries(imageModules).map(([path, url]) => {
    const relativePath = path.split("/user-guide/").pop() ?? path;
    return [relativePath, url];
  }),
);

export function getGuideDocument(slug?: string): GuideDocument | undefined {
  return bySlug.get(slug ?? "start");
}

export function getGuideNeighbors(slug: string) {
  const index = guideDocuments.findIndex((doc) => doc.slug === slug);
  return {
    previous: index > 0 ? guideDocuments[index - 1] : undefined,
    next: index >= 0 && index < guideDocuments.length - 1
      ? guideDocuments[index + 1]
      : undefined,
  };
}

export function resolveGuideHref(href?: string) {
  if (!href || /^(https?:|mailto:|tel:|#)/i.test(href)) return undefined;
  const filename = href.split("#")[0].split("/").pop()?.toLowerCase();
  if (!filename) return undefined;
  const slug = slugByFilename.get(filename);
  if (!slug) return undefined;
  const hash = href.includes("#") ? `#${href.split("#").slice(1).join("#")}` : "";
  return `/guide/${slug}${hash}`;
}

export function resolveGuideImage(src?: string) {
  if (!src || /^(https?:|data:|blob:|\/\/)/i.test(src)) return src;
  return imagesByPath.get(src.replace(/^\.\//, "")) ?? src;
}

export function stripFirstHeading(markdown: string) {
  return markdown.replace(/^#\s+.+(?:\r?\n)+/, "");
}

export function headingId(text: string) {
  return text
    .toLocaleLowerCase("vi")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/đ/g, "d")
    .replace(/`/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

export function guideHeadings(markdown: string): GuideHeading[] {
  const headings: GuideHeading[] = [];
  let insideFence = false;
  for (const line of markdown.split(/\r?\n/)) {
    if (line.trimStart().startsWith("```")) {
      insideFence = !insideFence;
      continue;
    }
    if (insideFence) continue;
    const match = /^(##|###)\s+(.+)$/.exec(line);
    if (!match) continue;
    const text = match[2]
      .replace(/`([^`]+)`/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
      .trim();
    headings.push({
      depth: match[1].length as 2 | 3,
      id: headingId(text),
      text,
    });
  }
  return headings;
}

export function guideReadingMinutes(markdown: string) {
  const words = markdown
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[#>*_`\-[\]()]/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean).length;
  return Math.max(1, Math.ceil(words / 220));
}
