"use client";

import React, { useState } from "react";

/**
 * Compact, collapsible JSON tree viewer — replaces raw JSON.stringify dumps with
 * a readable, colour-coded, expandable structure. Top two levels open by default.
 */

const KEY = "#6d28d9";
const STR = "#047857";
const NUM = "#b45309";
const BOOL = "#be123c";
const NIL = "#94a3b8";

function Primitive({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span style={{ color: NIL }}>null</span>;
  if (typeof value === "string")
    return <span style={{ color: STR, wordBreak: "break-word" }}>{value || '""'}</span>;
  if (typeof value === "number") return <span style={{ color: NUM }}>{String(value)}</span>;
  if (typeof value === "boolean") return <span style={{ color: BOOL }}>{String(value)}</span>;
  return <span>{String(value)}</span>;
}

function Node({ k, value, depth }: { k?: string; value: unknown; depth: number }) {
  const isObj = value !== null && typeof value === "object";
  const [open, setOpen] = useState(depth < 2);

  if (!isObj) {
    return (
      <div>
        {k !== undefined && <span style={{ color: KEY, fontWeight: 600 }}>{k}: </span>}
        <Primitive value={value} />
      </div>
    );
  }

  const isArr = Array.isArray(value);
  const entries: [string, unknown][] = isArr
    ? (value as unknown[]).map((v, i) => [String(i), v])
    : Object.entries(value as Record<string, unknown>);
  const summary = isArr ? `array[${entries.length}]` : `{${entries.length}}`;

  return (
    <div>
      <span
        onClick={() => setOpen((o) => !o)}
        style={{ cursor: "pointer", userSelect: "none" }}
      >
        <i
          className={`bi bi-chevron-${open ? "down" : "right"}`}
          style={{ fontSize: "0.55rem", marginRight: 4, color: "#94a3b8" }}
        />
        {k !== undefined && <span style={{ color: KEY, fontWeight: 600 }}>{k} </span>}
        <span style={{ color: "#94a3b8" }}>{summary}</span>
      </span>
      {open && entries.length > 0 && (
        <div style={{ paddingLeft: 14, borderLeft: "1px solid #eef2f7", marginLeft: 4 }}>
          {entries.map(([ck, cv]) => (
            <Node key={ck} k={ck} value={cv} depth={depth + 1} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function JsonTree({ data }: { data: unknown }) {
  return (
    <div
      style={{
        fontFamily: "var(--bs-font-monospace, monospace)",
        fontSize: "0.72rem",
        lineHeight: 1.6,
        color: "#334155",
      }}
    >
      <Node value={data} depth={0} />
    </div>
  );
}
