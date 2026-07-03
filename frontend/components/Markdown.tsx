"use client";

import React from "react";

/**
 * Lightweight, dependency-free Markdown renderer.
 *
 * Covers exactly what the Podio agent emits: headings, bold/italic, inline code,
 * fenced code blocks, GFM tables, bullet/numbered lists, horizontal rules, links,
 * and paragraphs. Not a full CommonMark implementation — just enough to make the
 * agent's structured replies readable instead of raw text.
 */

const codeStyle: React.CSSProperties = {
  background: "rgba(99,102,241,0.1)",
  color: "#4338ca",
  padding: "0.1em 0.35em",
  borderRadius: 4,
  fontSize: "0.85em",
  fontFamily: "var(--bs-font-monospace, monospace)",
};

const inlineRe = /(\*\*([^*]+)\*\*|\*([^*\n]+)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/g;

function renderInline(text: string, kp: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let last = 0;
  let i = 0;
  let m: RegExpExecArray | null;
  inlineRe.lastIndex = 0;
  while ((m = inlineRe.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    if (m[2] !== undefined) {
      nodes.push(<strong key={`${kp}-b${i}`}>{m[2]}</strong>);
    } else if (m[3] !== undefined) {
      nodes.push(<em key={`${kp}-i${i}`}>{m[3]}</em>);
    } else if (m[4] !== undefined) {
      nodes.push(
        <code key={`${kp}-c${i}`} style={codeStyle}>
          {m[4]}
        </code>,
      );
    } else if (m[5] !== undefined) {
      nodes.push(
        <a key={`${kp}-a${i}`} href={m[6]} target="_blank" rel="noreferrer">
          {m[5]}
        </a>,
      );
    }
    last = inlineRe.lastIndex;
    i++;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** Render text that may contain hard line breaks (within a paragraph / table cell). */
function renderMultiline(text: string, kp: string): React.ReactNode[] {
  const lines = text.split("\n");
  const out: React.ReactNode[] = [];
  lines.forEach((line, idx) => {
    if (idx > 0) out.push(<br key={`${kp}-br${idx}`} />);
    out.push(...renderInline(line, `${kp}-l${idx}`));
  });
  return out;
}

function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

function isBlockStart(line: string): boolean {
  const t = line.trim();
  return (
    t === "" ||
    t.startsWith("```") ||
    /^#{1,6}\s+/.test(t) ||
    /^[-*+]\s+/.test(t) ||
    /^\d+\.\s+/.test(t) ||
    /^(-{3,}|\*{3,}|_{3,})$/.test(t) ||
    (t.includes("|") && line.includes("|"))
  );
}

function toText(content: unknown): string {
  if (typeof content === "string") return content;
  if (content === null || content === undefined) return "";
  try {
    return JSON.stringify(content, null, 2);
  } catch {
    return String(content);
  }
}

export default function Markdown({ content }: { content: unknown }) {
  const lines = toText(content).replace(/\r\n/g, "\n").split("\n");
  const blocks: React.ReactNode[] = [];
  let i = 0;
  let key = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (trimmed === "") {
      i++;
      continue;
    }

    // Fenced code block
    if (trimmed.startsWith("```")) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        buf.push(lines[i]);
        i++;
      }
      i++; // skip closing fence
      blocks.push(
        <pre
          key={key++}
          style={{
            background: "#0f172a",
            color: "#e2e8f0",
            padding: "0.75rem 0.9rem",
            borderRadius: 8,
            overflowX: "auto",
            fontSize: "0.8rem",
            margin: "0 0 0.6rem",
          }}
        >
          <code>{buf.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    // Heading (# .. ######) — mapped down so it fits inside a chat bubble
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      const level = Math.min(h[1].length + 2, 6);
      blocks.push(
        React.createElement(
          `h${level}`,
          {
            key: key++,
            style: { fontSize: level <= 3 ? "1rem" : "0.9rem", fontWeight: 700, margin: "0.4rem 0 0.3rem" },
          },
          renderInline(h[2], `h${key}`),
        ),
      );
      i++;
      continue;
    }

    // Horizontal rule
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      blocks.push(<hr key={key++} style={{ margin: "0.5rem 0", borderColor: "#e2e8f0" }} />);
      i++;
      continue;
    }

    // GFM table: a header row followed by a |---|---| separator row
    if (
      line.includes("|") &&
      i + 1 < lines.length &&
      lines[i + 1].includes("-") &&
      /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])
    ) {
      const header = splitRow(line);
      i += 2; // skip header + separator
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|") && lines[i].trim() !== "") {
        rows.push(splitRow(lines[i]));
        i++;
      }
      blocks.push(
        <div key={key++} style={{ overflowX: "auto", margin: "0 0 0.6rem" }}>
          <table className="table table-sm table-bordered mb-0" style={{ fontSize: "0.8rem" }}>
            <thead>
              <tr>
                {header.map((c, ci) => (
                  <th key={ci} style={{ background: "#f1f5f9", whiteSpace: "nowrap" }}>
                    {renderInline(c, `th${key}-${ci}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri}>
                  {header.map((_, ci) => (
                    <td key={ci}>{renderInline(r[ci] ?? "", `td${key}-${ri}-${ci}`)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }

    // Unordered list
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*+]\s+/, ""));
        i++;
      }
      blocks.push(
        <ul key={key++} style={{ margin: "0 0 0.5rem", paddingLeft: "1.2rem" }}>
          {items.map((it, ii) => (
            <li key={ii}>{renderInline(it, `ul${key}-${ii}`)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    // Ordered list
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, ""));
        i++;
      }
      blocks.push(
        <ol key={key++} style={{ margin: "0 0 0.5rem", paddingLeft: "1.2rem" }}>
          {items.map((it, ii) => (
            <li key={ii}>{renderInline(it, `ol${key}-${ii}`)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    // Paragraph: gather consecutive lines until a blank line or new block
    const para: string[] = [line];
    i++;
    while (i < lines.length && lines[i].trim() !== "" && !isBlockStart(lines[i])) {
      para.push(lines[i]);
      i++;
    }
    blocks.push(
      <p key={key++} style={{ margin: "0 0 0.5rem" }}>
        {renderMultiline(para.join("\n"), `p${key}`)}
      </p>,
    );
  }

  // Strip the trailing margin of the last block so bubbles stay tight.
  return <div className="md-body" style={{ marginBottom: "-0.5rem" }}>{blocks}</div>;
}
