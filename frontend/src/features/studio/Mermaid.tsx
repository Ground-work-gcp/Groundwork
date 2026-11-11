import { useEffect, useId, useRef, useState } from "react";

/* Renders Mermaid source to SVG. The library (~1 MB) is loaded only when a diagram is shown,
   in strict security mode (no HTML labels, no click handlers). Falls back to the source text. */

let loader: Promise<typeof import("mermaid").default> | null = null;
function load() {
  loader ??= import("mermaid").then((m) => {
    const dark = window.matchMedia?.("(prefers-color-scheme: dark)").matches;
    m.default.initialize({ startOnLoad: false, securityLevel: "strict", theme: dark ? "dark" : "neutral", flowchart: { htmlLabels: false } });
    return m.default;
  });
  return loader;
}

export function Mermaid({ code, title = "Diagram" }: { code: string; title?: string }) {
  const host = useRef<HTMLDivElement>(null);
  const id = "mmd" + useId().replace(/[^a-zA-Z0-9]/g, "");
  const [error, setError] = useState<string | null>(null);
  const [showSource, setShowSource] = useState(false);

  useEffect(() => {
    let alive = true;
    setError(null);
    // strict mode can't render <br/>/<small> labels; flatten them to plain text
    const clean = code.replace(/<br\s*\/?>/g, " · ").replace(/<\/?small>/g, "");
    load()
      .then((m) => m.render(id, clean))
      .then(({ svg }) => {
        if (alive && host.current) host.current.innerHTML = svg;
      })
      .catch((e) => alive && setError(e instanceof Error ? e.message.split("\n")[0] : String(e)));
    return () => {
      alive = false;
    };
  }, [code, id]);

  return (
    <figure className="mermaid-fig" aria-label={title}>
      <div className="row">
        <button className="btn ghost" onClick={() => void navigator.clipboard?.writeText(code)}>Copy source</button>
        <button className="btn ghost" onClick={() => setShowSource((v) => !v)}>{showSource ? "Hide" : "Show"} source</button>
        <button className="btn ghost" onClick={() => {
          const svg = host.current?.querySelector("svg")?.outerHTML;
          if (!svg) return;
          const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
          Object.assign(document.createElement("a"), { href: url, download: `${title.toLowerCase().replace(/\W+/g, "-")}.svg` }).click();
          URL.revokeObjectURL(url);
        }}>SVG</button>
      </div>
      {error ? <p className="err">Diagram could not be rendered: {error}</p> : null}
      <div ref={host} className="mermaid-svg" />
      {showSource || error ? <pre className="code">{code}</pre> : null}
    </figure>
  );
}
