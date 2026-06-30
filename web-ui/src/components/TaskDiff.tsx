import { DiffEditor, Editor } from "@monaco-editor/react";
import type { editor as MonacoEditor } from "monaco-editor";
import { useCallback, useMemo, useRef, useState } from "react";
import "@/lib/monaco-setup";
import { monacoLanguageFromPath } from "@/lib/monacoLanguage";
import { useIsDark } from "@/lib/useIsDark";
import { cn } from "@/lib/utils";

export type DiffMode = "diff" | "old" | "new";

const SHARED_OPTIONS: MonacoEditor.IEditorConstructionOptions = {
  readOnly: true,
  renderValidationDecorations: "off",
  scrollBeyondLastLine: false,
  minimap: { enabled: false },
  automaticLayout: true,
  fontSize: 12.5,
  lineNumbersMinChars: 3,
  folding: true,
  scrollbar: { alwaysConsumeMouseWheel: false },
};

/**
 * Read-only Monaco diff/source view for one file. `diff` mode renders a
 * side-by-side (or inline, for pure add/delete) diff with **unchanged regions
 * folded** — the key to reviewing large files. `old`/`new` modes show a single
 * editor. Height auto-grows to the content (capped by `maxHeight`, after which
 * the editor scrolls) so cards size to their diff instead of nesting scrollbars.
 */
export function TaskDiff({
  original,
  modified,
  path,
  mode = "diff",
  className,
  maxHeight = 720,
  minHeight = 80,
}: {
  original: string;
  modified: string;
  path: string;
  mode?: DiffMode;
  className?: string;
  maxHeight?: number;
  minHeight?: number;
}) {
  const dark = useIsDark();
  const theme = dark ? "agent-team-dark" : "agent-team-light";
  const language = useMemo(() => monacoLanguageFromPath(path), [path]);
  const [height, setHeight] = useState(minHeight);

  const isAdded = original === "" && modified !== "";
  const isDeleted = modified === "" && original !== "";
  const sideBySide = mode === "diff" && !isAdded && !isDeleted;

  const clamp = useCallback(
    (h: number) => Math.min(Math.max(h + 16, minHeight), maxHeight),
    [maxHeight, minHeight],
  );

  const diffRef = useRef<MonacoEditor.IStandaloneDiffEditor | null>(null);
  const onDiffMount = useCallback(
    (ed: MonacoEditor.IStandaloneDiffEditor) => {
      diffRef.current = ed;
      const recompute = () => {
        const o = ed.getOriginalEditor().getContentHeight();
        const m = ed.getModifiedEditor().getContentHeight();
        setHeight(clamp(Math.max(o, m)));
      };
      recompute();
      ed.getOriginalEditor().onDidContentSizeChange(recompute);
      ed.getModifiedEditor().onDidContentSizeChange(recompute);
    },
    [clamp],
  );

  const singleRef = useRef<MonacoEditor.IStandaloneCodeEditor | null>(null);
  const onSingleMount = useCallback(
    (ed: MonacoEditor.IStandaloneCodeEditor) => {
      singleRef.current = ed;
      const recompute = () => setHeight(clamp(ed.getContentHeight()));
      recompute();
      ed.onDidContentSizeChange(recompute);
    },
    [clamp],
  );

  if (mode === "diff") {
    return (
      <div className={cn("w-full", className)} style={{ height }}>
        <DiffEditor
          className="h-full w-full"
          language={language}
          original={original}
          modified={modified}
          theme={theme}
          onMount={onDiffMount}
          options={{
            ...SHARED_OPTIONS,
            renderSideBySide: sideBySide,
            hideUnchangedRegions: { enabled: true },
            renderOverviewRuler: false,
          }}
        />
      </div>
    );
  }

  const value = mode === "old" ? original : modified;
  return (
    <div className={cn("w-full", className)} style={{ height }}>
      <Editor
        className="h-full w-full"
        language={language}
        value={value}
        theme={theme}
        onMount={onSingleMount}
        options={SHARED_OPTIONS}
      />
    </div>
  );
}
