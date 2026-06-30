/**
 * Bundle Monaco locally (no CDN fetch) and register the app's editor themes.
 *
 * The plugin self-hosts its assets and may run offline, so we point
 * `@monaco-editor/react` at the npm `monaco-editor` instance instead of its
 * default jsDelivr loader. We only wire the base **editor** worker: every Monaco
 * surface in this app is read-only (diffs / file previews), so the language
 * service workers (ts/json/css/html — diagnostics, completion) aren't needed,
 * and Monarch tokenizing still gives syntax highlight on the main thread. The
 * editor worker is what computes diffs, so it must be present.
 *
 * Import this module once before any Monaco component mounts (TaskDiff does).
 */
import { loader } from "@monaco-editor/react";
import * as monaco from "monaco-editor";
import EditorWorker from "monaco-editor/esm/vs/editor/editor.worker?worker";

declare global {
  interface Window {
    MonacoEnvironment?: monaco.Environment;
  }
}

let configured = false;

/** Idempotently bundle Monaco + register themes. Safe to call repeatedly. */
export function setupMonaco(): void {
  if (configured) return;
  configured = true;

  self.MonacoEnvironment = {
    getWorker() {
      return new EditorWorker();
    },
  };

  // Dark theme — deep cool-grey editor surface with legible diff bands, matched
  // to the app's `--code-bg` token so the editor reads one tier deeper than the
  // surrounding cards.
  monaco.editor.defineTheme("agent-team-dark", {
    base: "vs-dark",
    inherit: true,
    rules: [
      { token: "comment", foreground: "6a9955" },
      { token: "keyword", foreground: "569cd6" },
      { token: "string", foreground: "ce9178" },
      { token: "number", foreground: "b5cea8" },
    ],
    colors: {
      // Matches `--code-bg` (#0E1116) in the `.code-scope` so the editor reads
      // as one tier deeper than the surrounding diff cards (#16191F).
      "editor.background": "#0E1116",
      "editorGutter.background": "#0E1116",
      "editorLineNumber.foreground": "#4B5468",
      "editorLineNumber.activeForeground": "#A3B0C4",
      "editorIndentGuide.background1": "#1B1F27",
      "editorIndentGuide.activeBackground1": "#2A303B",
      "editor.lineHighlightBackground": "#16191F",
      "editor.selectionBackground": "#234166",
      "diffEditor.insertedTextBackground": "#1f7a3f4d",
      "diffEditor.removedTextBackground": "#a032324d",
      "diffEditor.insertedLineBackground": "#10381f66",
      "diffEditor.removedLineBackground": "#4a141466",
      "diffEditor.border": "#2A303B",
      "diffEditorGutter.insertedLineBackground": "#10381f66",
      "diffEditorGutter.removedLineBackground": "#4a141466",
      "editorUnnecessaryCode.border": "#00000000",
    },
  });

  // Light theme — keeps the Jira-ish light canvas the app uses today.
  monaco.editor.defineTheme("agent-team-light", {
    base: "vs",
    inherit: true,
    rules: [],
    colors: {
      "editor.background": "#FFFFFF",
      "diffEditor.insertedTextBackground": "#abf2bc66",
      "diffEditor.removedTextBackground": "#ffd7d566",
      "diffEditor.insertedLineBackground": "#e6ffec",
      "diffEditor.removedLineBackground": "#ffebe9",
      "diffEditor.border": "#DFE1E6",
    },
  });

  loader.config({ monaco });
}

setupMonaco();

export { monaco };
