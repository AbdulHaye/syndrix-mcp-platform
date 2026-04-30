"use client";

import { useState } from "react";

interface Props {
  label: string;
  field: string;
  data: Record<string, unknown>;
}

export default function TextResult({ label, field, data }: Props) {
  const [copied, setCopied] = useState(false);
  const text = String(data[field] ?? "");

  if (data.success === false) {
    return (
      <div className="alert alert-danger small">
        <i className="bi bi-exclamation-triangle me-2" />
        {String(data.error ?? "Failed")}
      </div>
    );
  }

  if (!text) return null;

  function copy() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="border rounded-3 overflow-hidden">
      <div className="d-flex align-items-center justify-content-between px-3 py-2 border-bottom bg-light">
        <span className="small fw-semibold text-muted">{label}</span>
        <button className="btn btn-sm btn-link text-muted p-0" onClick={copy}>
          <i className={`bi ${copied ? "bi-check2 text-success" : "bi-clipboard"} me-1`} />
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre
        style={{
          margin: 0, padding: "1rem", fontSize: "0.82rem",
          maxHeight: 480, overflowY: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word",
          background: "var(--bs-body-bg)", color: "var(--bs-body-color)",
        }}
      >
        {text}
      </pre>
    </div>
  );
}
