import { describe, expect, it } from "vitest";
import { filePriorityScore, sortFilesByPriority } from "./filePriority";

describe("filePriorityScore", () => {
  it("ranks known entrypoints ahead of secondary and generic files", () => {
    expect(filePriorityScore("index.html")).toBeLessThan(
      filePriorityScore("README.md"),
    );
    expect(filePriorityScore("README.md")).toBeLessThan(
      filePriorityScore("tsconfig.json"),
    );
    expect(filePriorityScore("tsconfig.json")).toBeLessThan(
      filePriorityScore("src/utils/helpers.ts"),
    );
  });

  it("is case-insensitive on basenames", () => {
    expect(filePriorityScore("ReadMe.md")).toBe(filePriorityScore("readme.md"));
  });
});

describe("sortFilesByPriority", () => {
  it("puts shallower paths before deeper ones", () => {
    const sorted = sortFilesByPriority([
      "src/components/Button.tsx",
      "README.md",
      "a/b/c/deep.ts",
    ]);
    expect(sorted[0]).toBe("README.md");
    expect(sorted[sorted.length - 1]).toBe("a/b/c/deep.ts");
  });

  it("breaks depth ties by basename importance then alphabetically", () => {
    const sorted = sortFilesByPriority(["zzz.ts", "package.json", "app.py"]);
    // package.json + app.py (entrypoints) precede the generic zzz.ts.
    expect(sorted.indexOf("package.json")).toBeLessThan(sorted.indexOf("zzz.ts"));
    expect(sorted.indexOf("app.py")).toBeLessThan(sorted.indexOf("zzz.ts"));
  });

  it("does not mutate the input array", () => {
    const input = ["b.ts", "a.ts"];
    const copy = [...input];
    sortFilesByPriority(input);
    expect(input).toEqual(copy);
  });
});
