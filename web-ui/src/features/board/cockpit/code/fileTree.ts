/**
 * Pure helpers for building the "changed only" synthetic file tree from a flat
 * list of workspace-relative paths. Kept free of React so it's unit-testable.
 */

export interface SynNode {
  name: string;
  path: string;
  kind: "file" | "dir";
  children: SynNode[];
}

/** Sort in place: directories first, then alphabetical; recurses into children. */
export function sortNodes(nodes: SynNode[]): SynNode[] {
  nodes.sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "dir" ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
  for (const n of nodes) if (n.children.length) sortNodes(n.children);
  return nodes;
}

/**
 * Build a nested tree from flat paths (e.g. `["a/b.ts", "a/c.ts", "d.ts"]`).
 * Intermediate segments become `dir` nodes; the final segment is a `file`.
 * Result is sorted (dirs first, alphabetical).
 */
export function buildSyntheticTree(paths: string[]): SynNode[] {
  const roots: SynNode[] = [];
  const index = new Map<string, SynNode>();
  for (const full of [...paths].sort()) {
    const parts = full.split("/").filter(Boolean);
    let prefix = "";
    let siblings = roots;
    parts.forEach((part, i) => {
      prefix = prefix ? `${prefix}/${part}` : part;
      const isLeaf = i === parts.length - 1;
      let node = index.get(prefix);
      if (!node) {
        node = {
          name: part,
          path: prefix,
          kind: isLeaf ? "file" : "dir",
          children: [],
        };
        index.set(prefix, node);
        siblings.push(node);
      }
      siblings = node.children;
    });
  }
  return sortNodes(roots);
}
