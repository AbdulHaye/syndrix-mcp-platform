"use client";

import { useRef, useState } from "react";

interface Props {
  onFileRead: (name: string, text: string) => void;
}

const ACCEPT = ".txt,.md,.csv,.json";

export default function FileUpload({ onFileRead }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [status, setStatus] = useState<{ name: string; size: string } | null>(null);
  const [error, setError] = useState("");

  function readFile(file: File) {
    setError("");
    const ext = file.name.split(".").pop()?.toLowerCase();
    if (!["txt", "md", "csv", "json"].includes(ext ?? "")) {
      setError(`Unsupported file type ".${ext}". Accepted: txt, md, csv, json.`);
      return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      const text = e.target?.result as string;
      setStatus({ name: file.name, size: (file.size / 1024).toFixed(1) + " KB" });
      onFileRead(file.name, text);
    };
    reader.readAsText(file);
  }

  function onDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragging(false);
    const file = e.dataTransfer.files[0];
    if (file) readFile(file);
  }

  function onChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) readFile(file);
  }

  return (
    <div>
      <div
        className={`border-2 border-dashed rounded-3 p-4 text-center${dragging ? " border-primary bg-primary bg-opacity-10" : ""}`}
        style={{ borderStyle: "dashed", cursor: "pointer" }}
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => inputRef.current?.click()}
      >
        <input ref={inputRef} type="file" accept={ACCEPT} className="d-none" onChange={onChange} />
        <i className="bi bi-cloud-upload fs-3 text-muted" />
        <div className="mt-2 small text-muted">
          Drag &amp; drop or <span className="text-primary fw-semibold">browse</span>
        </div>
        <div className="mt-1" style={{ fontSize: "0.7rem", color: "#adb5bd" }}>
          Accepts: .txt, .md, .csv, .json
        </div>
      </div>

      {error && (
        <div className="alert alert-danger small py-2 mt-2 mb-0">
          <i className="bi bi-exclamation-triangle me-2" />{error}
        </div>
      )}

      {status && !error && (
        <div className="alert alert-success small py-2 mt-2 mb-0 d-flex align-items-center gap-2">
          <i className="bi bi-file-earmark-check-fill text-success" />
          <span>
            <strong>{status.name}</strong> loaded ({status.size}) — content populated below
          </span>
          <button
            className="btn-close ms-auto"
            style={{ fontSize: "0.6rem" }}
            onClick={(e) => { e.stopPropagation(); setStatus(null); }}
          />
        </div>
      )}
    </div>
  );
}
