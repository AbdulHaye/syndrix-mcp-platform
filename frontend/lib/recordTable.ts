"use client";

// Generic "array of records -> table + CSV" helpers, used to render MyCase (and any
// future) tool results as a real spreadsheet-style table instead of a raw JSON tree,
// with a matching CSV export. Deliberately shape-agnostic: derives columns from the
// union of keys actually present across the items, rather than hardcoding per-resource
// column lists (MyCase alone has 20+ distinct record shapes).

export type RecordRow = Record<string, unknown>;

/** Flatten one field value to a single display-safe string (used in both the table
 * cell and the CSV cell). Nested objects prefer a human label (name/first+last name)
 * over dumping raw JSON; arrays are joined; primitives pass through as-is. */
export function flattenValue(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v !== "object") return String(v);
  if (Array.isArray(v)) {
    if (v.length === 0) return "";
    return v.map(flattenValue).filter(Boolean).join("; ");
  }
  const obj = v as RecordRow;
  const first = obj.first_name as string | undefined;
  const last = obj.last_name as string | undefined;
  const label =
    obj.name ?? obj.label ?? obj.title ?? obj.description ??
    (first || last ? [first, last].filter(Boolean).join(" ") : undefined) ??
    obj.value ??
    obj.id;
  if (label !== undefined && label !== null && typeof label !== "object") {
    // Include email alongside the name when present — a case's client/staff/
    // contact is usually flattened into a single table cell (see
    // primaryResultGroups in mycase-agent, which deliberately keeps related
    // objects as cell values rather than splitting them into separate tables),
    // so the email would otherwise be silently lost.
    const email = typeof obj.email === "string" && obj.email ? obj.email : undefined;
    return email ? `${label} <${email}>` : String(label);
  }
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

/** Union of top-level keys across all rows, in first-seen order — this is what makes
 * the table include every column that appears anywhere in the result set, not just
 * whatever the first row happens to have. */
export function deriveColumns(items: RecordRow[]): string[] {
  const seen = new Set<string>();
  const columns: string[] = [];
  for (const item of items) {
    if (!item || typeof item !== "object") continue;
    for (const key of Object.keys(item)) {
      if (!seen.has(key)) {
        seen.add(key);
        columns.push(key);
      }
    }
  }
  return columns;
}

function csvEscape(value: string): string {
  if (/[",\n]/.test(value)) {
    return `"${value.replace(/"/g, '""')}"`;
  }
  return value;
}

export function itemsToCsv(items: RecordRow[], columns: string[]): string {
  const header = columns.map(csvEscape).join(",");
  const rows = items.map((item) =>
    columns.map((col) => csvEscape(flattenValue(item[col]))).join(",")
  );
  return [header, ...rows].join("\r\n");
}

export function downloadCsv(filename: string, csv: string): void {
  // Prefix a UTF-8 BOM so Excel opens the file with correct encoding rather than
  // mis-reading non-ASCII names as garbled text.
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename.endsWith(".csv") ? filename : `${filename}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
