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

type ActiveTool = "search" | "ingest" | null;

export default function RagPage() {
  const toast = useToast();
  const [active, setActive] = useState<ActiveTool>(null);

  // Search state
  const [searchQuery, setSearchQuery] = useState("");
  const [searchLimit, setSearchLimit] = useState(5);
  const [searchInvocation, setSearchInvocation] = useState<ToolInvokeResult | null>(null);
  const [searchLoading, setSearchLoading] = useState(false);

  // Ingest state
  const [ingestTitle, setIngestTitle] = useState("");
  const [ingestContent, setIngestContent] = useState("");
  const [ingestSource, setIngestSource] = useState("manual");
  const [ingestChunkSize, setIngestChunkSize] = useState(400);
  const [ingestResult, setIngestResult] = useState<IngestResult | null>(null);
  const [ingestLoading, setIngestLoading] = useState(false);

  function openTool(tool: ActiveTool) {
    setActive(tool);
    setSearchInvocation(null);
    setIngestResult(null);
  }

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

  return (
    <>
      <Topbar title="Knowledge Base" subtitle="RAG · Semantic Search · Document Ingestion" />
      <div className="page-body">
        <div className="row g-3 mb-4">
          <div className="col-sm-6 col-lg-4">
            <ToolCard
              icon="bi-search"
              iconBg="#6f42c1"
              title="Semantic Search"
              description="Query the knowledge base using natural language."
              onClick={() => openTool("search")}
              badge={active === "search" ? "active" : undefined}
              badgeColor="primary"
            />
          </div>
          <div className="col-sm-6 col-lg-4">
            <ToolCard
              icon="bi-cloud-upload"
              iconBg="#0dcaf0"
              title="Ingest Document"
              description="Add a text document to the knowledge base."
              onClick={() => openTool("ingest")}
              badge={active === "ingest" ? "active" : undefined}
              badgeColor="primary"
            />
          </div>
        </div>

        {/* Search panel */}
        {active === "search" && (
          <div className="card border p-4 mb-4">
            <div className="d-flex align-items-center justify-content-between mb-3">
              <h6 className="fw-bold mb-0 d-flex align-items-center gap-2">
                <i className="bi bi-search text-primary" />
                Semantic Search
              </h6>
              <button className="btn btn-sm btn-outline-secondary" onClick={() => setActive(null)}>
                <i className="bi bi-x" />
              </button>
            </div>

            <div className="row g-3 mb-3">
              <div className="col-md-9">
                <label className="form-label small fw-semibold">Query</label>
                <input
                  className="form-control"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="What are the contract terms for Acme Corp?"
                  onKeyDown={(e) => e.key === "Enter" && !searchLoading && searchQuery && runSearch()}
                />
              </div>
              <div className="col-md-3">
                <label className="form-label small fw-semibold">Results limit</label>
                <input
                  type="number"
                  className="form-control"
                  value={searchLimit}
                  onChange={(e) => setSearchLimit(Number(e.target.value))}
                  min={1}
                  max={20}
                />
              </div>
            </div>

            <button className="btn btn-primary mb-3" onClick={runSearch} disabled={searchLoading || !searchQuery}>
              {searchLoading
                ? <><span className="spinner-border spinner-border-sm me-2" />Searching…</>
                : <><i className="bi bi-search me-2" />Search</>}
            </button>

            {searchLoading && <SkeletonTable rows={searchLimit} cols={3} />}
            {!searchLoading && searchInvocation && (
              <div>
                <div className="small fw-semibold text-muted mb-2">Results</div>
                <ResultRenderer invocation={searchInvocation} />
              </div>
            )}
          </div>
        )}

        {/* Ingest panel */}
        {active === "ingest" && (
          <div className="card border p-4">
            <div className="d-flex align-items-center justify-content-between mb-3">
              <h6 className="fw-bold mb-0 d-flex align-items-center gap-2">
                <i className="bi bi-cloud-upload text-info" />
                Ingest Document
              </h6>
              <button className="btn btn-sm btn-outline-secondary" onClick={() => setActive(null)}>
                <i className="bi bi-x" />
              </button>
            </div>

            <div className="mb-4">
              <label className="form-label small fw-semibold">Upload a file <span className="text-muted fw-normal">(auto-fills content below)</span></label>
              <FileUpload onFileRead={onFileRead} />
            </div>

            <div className="row g-3">
              <div className="col-md-8">
                <label className="form-label small fw-semibold">Document Title</label>
                <input
                  className="form-control"
                  value={ingestTitle}
                  onChange={(e) => setIngestTitle(e.target.value)}
                  placeholder="e.g. Acme Corp Contract 2026"
                />
              </div>
              <div className="col-md-4">
                <label className="form-label small fw-semibold">Source</label>
                <input
                  className="form-control"
                  value={ingestSource}
                  onChange={(e) => setIngestSource(e.target.value)}
                  placeholder="manual / url / filename"
                />
              </div>
              <div className="col-12">
                <label className="form-label small fw-semibold">Content</label>
                <textarea
                  className="form-control"
                  rows={8}
                  value={ingestContent}
                  onChange={(e) => setIngestContent(e.target.value)}
                  placeholder="Paste the document text here, or upload a file above…"
                />
              </div>
              <div className="col-md-4">
                <label className="form-label small fw-semibold">Chunk size (words)</label>
                <input
                  type="number"
                  className="form-control"
                  value={ingestChunkSize}
                  onChange={(e) => setIngestChunkSize(Number(e.target.value))}
                  min={50}
                  max={2000}
                />
              </div>
            </div>

            <button
              className="btn btn-info mt-3 mb-3"
              onClick={runIngest}
              disabled={ingestLoading || !ingestTitle || !ingestContent}
            >
              {ingestLoading
                ? <><span className="spinner-border spinner-border-sm me-2" />Ingesting…</>
                : <><i className="bi bi-cloud-upload me-2" />Ingest</>}
            </button>

            {ingestLoading && <SkeletonCard />}

            {!ingestLoading && ingestResult && (
              <div className="alert alert-success small">
                <i className="bi bi-check-circle me-2" />
                <strong>{ingestResult.chunks_stored}</strong> of {ingestResult.chunks_total} chunks stored.
                {ingestResult.chunks_failed > 0 && (
                  <span className="text-warning"> ({ingestResult.chunks_failed} failed)</span>
                )}
                <div className="mt-1 text-muted">
                  IDs: {ingestResult.document_ids.slice(0, 3).join(", ")}
                  {ingestResult.document_ids.length > 3 ? "…" : ""}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </>
  );
}
