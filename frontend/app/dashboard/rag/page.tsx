"use client";

import { useState } from "react";
import Topbar from "@/components/Topbar";
import ToolCard from "@/components/ToolCard";
import FileUpload from "@/components/FileUpload";
import ResultRenderer from "@/components/results/ResultRenderer";
import { SkeletonTable, SkeletonCard } from "@/components/Skeleton";
import { useToast } from "@/lib/toast";
import { invokeTool, ingestDocument } from "@/lib/api";
import type { ToolInvokeResult, IngestResult } from "@/types";

type ActiveTool = "search" | "ingest";

const TOOLS: { key: ActiveTool; icon: string; iconBg: string; color: string; bg: string; title: string; desc: string }[] = [
  { key: "search", icon: "bi-search",       iconBg: "#6f42c1", color: "#6f42c1", bg: "rgba(111,66,193,0.1)", title: "Semantic Search",  desc: "Query the knowledge base using natural language." },
  { key: "ingest", icon: "bi-cloud-upload", iconBg: "#0dcaf0", color: "#0dcaf0", bg: "rgba(13,202,240,0.1)", title: "Ingest Document",  desc: "Add a text document to the knowledge base." },
];

export default function RagPage() {
  const toast = useToast();
  const [active, setActive] = useState<ActiveTool | null>(null);

  const [searchQuery, setSearchQuery] = useState("");
  const [searchLimit, setSearchLimit] = useState(5);
  const [searchInvocation, setSearchInvocation] = useState<ToolInvokeResult | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);

  const [ingestTitle, setIngestTitle] = useState("");
  const [ingestContent, setIngestContent] = useState("");
  const [ingestSource, setIngestSource] = useState("manual");
  const [ingestChunkSize, setIngestChunkSize] = useState(150);
  const [ingestResult, setIngestResult] = useState<IngestResult | null>(null);
  const [ingestLoading, setIngestLoading] = useState(false);

  function openTool(tool: ActiveTool) {
    if (active !== tool) { setSearchInvocation(null); setIngestResult(null); }
    setActive(tool);
  }

  function goBack() { setActive(null); setSearchInvocation(null); setIngestResult(null); }

  async function runSearch() {
    setSearchLoading(true);
    setSearchInvocation(null);
    try {
      const res = await invokeTool({ tool: "rag.search", args: { query: searchQuery, limit: searchLimit } });
      setSearchInvocation(res);
      if (res.success) {
        const count = (res.result as Record<string, unknown[]>)?.results?.length ?? 0;
        toast.success(`Found ${count} result${count !== 1 ? "s" : ""}`);
      } else {
        toast.error(res.error ?? "Search failed");
      }
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Search failed");
    } finally {
      setSearchLoading(false);
    }
  }

  async function runIngest() {
    setIngestLoading(true);
    setIngestResult(null);
    try {
      const res = await ingestDocument({ title: ingestTitle, content: ingestContent, source: ingestSource, chunk_size: ingestChunkSize });
      setIngestResult(res);
      toast.success(`Ingested ${res.chunks_stored} chunks from "${ingestTitle}"`);
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Ingest failed");
    } finally {
      setIngestLoading(false);
    }
  }

  function onFileRead(name: string, text: string) {
    const ext = name.split(".").pop()?.toLowerCase();
    setIngestTitle(name.replace(/\.[^.]+$/, ""));
    setIngestContent(text);
    setIngestSource(ext === "json" ? "json" : ext === "csv" ? "csv" : "file");
    toast.info(`File "${name}" loaded — review and click Ingest`);
  }

  const activeTool = TOOLS.find((t) => t.key === active);

  /* ── Card grid view ── */
  if (!active) {
    return (
      <>
        <Topbar title="Knowledge Base" subtitle="RAG · Semantic Search · Document Ingestion" />
        <div className="page-body fade-in">
          <div className="row g-3">
            {TOOLS.map((t) => (
              <div key={t.key} className="col-sm-6 col-lg-4">
                <ToolCard icon={t.icon} iconBg={t.iconBg} title={t.title} description={t.desc} onClick={() => openTool(t.key)} />
              </div>
            ))}
          </div>
        </div>
      </>
    );
  }

  /* ── Split panel view ── */
  return (
    <>
      <Topbar title="Knowledge Base" subtitle="RAG · Semantic Search · Document Ingestion" />
      <div className="page-body fade-in">
        <div className="tool-split">

          {/* Left nav */}
          <nav className="tool-nav">
            <button className="tool-nav-back" onClick={goBack}>
              <i className="bi bi-arrow-left" />
              All Tools
            </button>
            {TOOLS.map((t) => (
              <button key={t.key} className={`tool-nav-item${active === t.key ? " active" : ""}`} onClick={() => openTool(t.key)}>
                <div className="tool-nav-icon" style={{ background: active === t.key ? "rgba(99,102,241,0.12)" : t.bg }}>
                  <i className={`bi ${t.icon}`} style={{ color: active === t.key ? "var(--primary)" : t.color }} />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: "0.845rem", fontWeight: 600, color: active === t.key ? "var(--primary)" : "var(--text)", lineHeight: 1.2 }}>{t.title}</div>
                  <div style={{ fontSize: "0.71rem", color: "var(--text-muted)", marginTop: "0.15rem", lineHeight: 1.3 }}>{t.desc}</div>
                </div>
                {active === t.key && <i className="bi bi-chevron-right" style={{ fontSize: "0.65rem", color: "var(--primary)", flexShrink: 0 }} />}
              </button>
            ))}
          </nav>

          {/* Right detail */}
          <div className="tool-detail">
            <div className="tool-detail-header">
              <div className="tool-detail-header-icon" style={{ background: activeTool!.bg }}>
                <i className={`bi ${activeTool!.icon}`} style={{ color: activeTool!.color }} />
              </div>
              <div>
                <div className="tool-detail-title">{activeTool!.title}</div>
                <div className="tool-detail-subtitle">{activeTool!.desc}</div>
              </div>
            </div>

            <div className="tool-detail-body">
              {active === "search" && (
                <>
                  <div className="row g-3 mb-3">
                    <div className="col-md-9">
                      <label className="form-label small fw-semibold">Query</label>
                      <input className="form-control" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
                        placeholder="What are the contract terms for Acme Corp?"
                        onKeyDown={(e) => e.key === "Enter" && !searchLoading && searchQuery && runSearch()} />
                    </div>
                    <div className="col-md-3">
                      <label className="form-label small fw-semibold">Limit</label>
                      <input type="number" className="form-control" value={searchLimit} onChange={(e) => setSearchLimit(Number(e.target.value))} min={1} max={20} />
                    </div>
                  </div>
                  <button className="btn btn-primary mb-3" style={{ background: "var(--primary)", border: "none" }} onClick={runSearch} disabled={searchLoading || !searchQuery}>
                    {searchLoading ? <><span className="spinner-border spinner-border-sm me-2" />Searching…</> : <><i className="bi bi-search me-2" />Search</>}
                  </button>
                  {searchLoading && <SkeletonTable rows={searchLimit} />}
                  {!searchLoading && searchInvocation && (
                    <div>
                      <div className="small fw-semibold text-muted mb-2">Results</div>
                      <ResultRenderer invocation={searchInvocation} />
                    </div>
                  )}
                </>
              )}

              {active === "ingest" && (
                <>
                  <div className="mb-4">
                    <label className="form-label small fw-semibold">
                      Upload a file <span className="text-muted fw-normal">(auto-fills content below)</span>
                    </label>
                    <FileUpload onFileRead={onFileRead} />
                  </div>
                  <div className="row g-3 mb-3">
                    <div className="col-md-8">
                      <label className="form-label small fw-semibold">Document Title</label>
                      <input className="form-control" value={ingestTitle} onChange={(e) => setIngestTitle(e.target.value)} placeholder="e.g. Acme Corp Contract 2026" />
                    </div>
                    <div className="col-md-4">
                      <label className="form-label small fw-semibold">Source</label>
                      <input className="form-control" value={ingestSource} onChange={(e) => setIngestSource(e.target.value)} placeholder="manual / url / filename" />
                    </div>
                    <div className="col-12">
                      <label className="form-label small fw-semibold">Content</label>
                      <textarea className="form-control" rows={8} value={ingestContent} onChange={(e) => setIngestContent(e.target.value)} placeholder="Paste the document text here, or upload a file above…" />
                    </div>
                    <div className="col-md-4">
                      <label className="form-label small fw-semibold">Chunk size (words)</label>
                      <input type="number" className="form-control" value={ingestChunkSize} onChange={(e) => setIngestChunkSize(Number(e.target.value))} min={50} max={2000} />
                    </div>
                  </div>
                  <button className="btn btn-info mb-3" onClick={runIngest} disabled={ingestLoading || !ingestTitle || !ingestContent}>
                    {ingestLoading ? <><span className="spinner-border spinner-border-sm me-2" />Ingesting…</> : <><i className="bi bi-cloud-upload me-2" />Ingest</>}
                  </button>
                  {ingestLoading && <SkeletonCard />}
                  {!ingestLoading && ingestResult && (
                    <div className="alert alert-success small">
                      <i className="bi bi-check-circle me-2" />
                      <strong>{ingestResult.chunks_stored}</strong> of {ingestResult.chunks_total} chunks stored.
                      {ingestResult.chunks_failed > 0 && <span className="text-warning"> ({ingestResult.chunks_failed} failed)</span>}
                      <div className="mt-1 text-muted">IDs: {ingestResult.document_ids.slice(0, 3).join(", ")}{ingestResult.document_ids.length > 3 ? "…" : ""}</div>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

        </div>
      </div>
    </>
  );
}
