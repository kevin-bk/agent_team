import { useEffect, useId, useState } from "react";
import mermaidScriptUrl from "mermaid/dist/mermaid.min.js?url";

interface MermaidApi {
  initialize: (configuration: {
    startOnLoad: boolean;
    securityLevel: "strict";
    theme: "dark" | "neutral";
    fontFamily: string;
    flowchart: { htmlLabels: boolean; curve: "basis" };
  }) => void;
  render: (id: string, source: string) => Promise<{ svg: string }>;
}

declare global {
  interface Window {
    mermaid?: MermaidApi;
  }
}

let mermaidPromise: Promise<MermaidApi> | undefined;

function loadMermaid() {
  if (window.mermaid) return Promise.resolve(window.mermaid);
  if (mermaidPromise) return mermaidPromise;
  mermaidPromise = new Promise<MermaidApi>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = mermaidScriptUrl;
    script.async = true;
    script.onload = () => {
      if (window.mermaid) resolve(window.mermaid);
      else reject(new Error("Mermaid loaded without exposing its renderer."));
    };
    script.onerror = () => reject(new Error("Unable to load the Mermaid renderer."));
    document.head.appendChild(script);
  });
  return mermaidPromise;
}

function currentTheme(): "dark" | "neutral" {
  return document.documentElement.classList.contains("dark") ? "dark" : "neutral";
}

export function MermaidDiagram({ source }: { source: string }) {
  const reactId = useId();
  const [svg, setSvg] = useState("");
  const [error, setError] = useState("");
  const [theme, setTheme] = useState(currentTheme);

  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(currentTheme()));
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function renderDiagram() {
      try {
        const mermaid = await loadMermaid();
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme,
          fontFamily: "Atlassian Sans, Inter, sans-serif",
          flowchart: { htmlLabels: true, curve: "basis" },
        });
        const id = `guide-mermaid-${reactId.replace(/[^a-zA-Z0-9]/g, "")}-${theme}`;
        const result = await mermaid.render(id, source);
        if (!cancelled) {
          setSvg(result.svg);
          setError("");
        }
      } catch (renderError) {
        if (!cancelled) {
          setSvg("");
          setError(
            renderError instanceof Error
              ? renderError.message
              : "Không thể hiển thị sơ đồ.",
          );
        }
      }
    }
    void renderDiagram();
    return () => {
      cancelled = true;
    };
  }, [reactId, source, theme]);

  if (error) {
    return (
      <div className="guide-diagram-error">
        <strong>Không thể hiển thị sơ đồ.</strong>
        <pre>{source}</pre>
      </div>
    );
  }

  if (!svg) {
    return (
      <div className="guide-diagram-loading" aria-label="Đang tải sơ đồ">
        <span />
        Đang dựng sơ đồ…
      </div>
    );
  }

  return (
    <div
      className="guide-diagram"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
