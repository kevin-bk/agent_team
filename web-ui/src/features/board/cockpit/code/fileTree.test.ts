import { describe, expect, it } from "vitest";
import { buildSyntheticTree, type SynNode } from "./fileTree";

function names(nodes: SynNode[]): string[] {
  return nodes.map((n) => n.name);
}

describe("buildSyntheticTree", () => {
  it("nests files under their directory segments", () => {
    const tree = buildSyntheticTree(["kb/src/a.ts", "kb/src/b.ts", "kb/readme.md"]);
    expect(names(tree)).toEqual(["kb"]);
    const kb = tree[0];
    expect(kb.kind).toBe("dir");
    // dir (src) before file (readme.md)
    expect(names(kb.children)).toEqual(["src", "readme.md"]);
    const src = kb.children[0];
    expect(src.kind).toBe("dir");
    expect(names(src.children)).toEqual(["a.ts", "b.ts"]);
    expect(src.children[0].path).toBe("kb/src/a.ts");
  });

  it("sorts directories before files, then alphabetically", () => {
    const tree = buildSyntheticTree(["z.ts", "a.ts", "dir/x.ts"]);
    expect(names(tree)).toEqual(["dir", "a.ts", "z.ts"]);
  });

  it("dedupes shared directory prefixes into one node", () => {
    const tree = buildSyntheticTree(["a/b/one.ts", "a/b/two.ts", "a/c.ts"]);
    const a = tree[0];
    expect(a.name).toBe("a");
    expect(names(a.children)).toEqual(["b", "c.ts"]);
    expect(a.children[0].children.length).toBe(2);
  });

  it("ignores empty/duplicate segments", () => {
    const tree = buildSyntheticTree(["a//b.ts", "a/b.ts"]);
    const a = tree[0];
    expect(a.children.length).toBe(1);
    expect(a.children[0].path).toBe("a/b.ts");
  });

  it("returns an empty array for no paths", () => {
    expect(buildSyntheticTree([])).toEqual([]);
  });
});
