interface RAGChunk {
  id: string;
  title: string;
  source: string;
  content: string;
  distance?: number | null;
}

interface Props {
  data: { query?: string; total?: number; results?: RAGChunk[]; success?: boolean; error?: string };
}

function scoreColor(distance?: number | null): string {
  if (distance == null) return "secondary";
  if (distance < 0.2) return "success";
  if (distance < 0.5) return "warning";
  return "danger";
}

function scoreLabel(distance?: number | null): string {
  if (distance == null) return "N/A";
  return (1 - distance).toFixed(2);
}

export default function RAGResults({ data }: Props) {
  if (data.success === false) {
    return (
      <div className="alert alert-danger small">
        <i className="bi bi-exclamation-triangle me-2" />
        {String(data.error ?? "Search failed")}
      </div>
    );
  }

  const results = data.results ?? [];

  if (!results.length) {
    return (
      <div className="text-center py-4 text-muted small">
        <i className="bi bi-journal-x me-2" />
        No matching documents found
      </div>
    );
  }

  return (
    <div>
      <div className="small text-muted mb-2">
        {data.total} chunk{data.total !== 1 ? "s" : ""} matched
        {data.query && <> for &quot;{data.query}&quot;</>}
      </div>
      <div className="d-flex flex-column gap-2">
        {results.map((chunk, i) => (
          <div key={chunk.id ?? i} className="border rounded-3 p-3 bg-white">
            <div className="d-flex align-items-start justify-content-between gap-2 mb-2">
              <div className="fw-semibold" style={{ fontSize: "0.88rem" }}>{chunk.title}</div>
              <span className={`badge bg-${scoreColor(chunk.distance)}-subtle text-${scoreColor(chunk.distance)} border border-${scoreColor(chunk.distance)}-subtle flex-shrink-0`}>
                {scoreLabel(chunk.distance)} match
              </span>
            </div>
            <div className="small text-muted mb-2">
              <i className="bi bi-folder me-1" />
              {chunk.source}
            </div>
            <div
              className="small p-2 rounded"
              style={{
                background: "var(--bs-secondary-bg, #f8f9fa)",
                fontSize: "0.78rem",
                maxHeight: 120,
                overflowY: "auto",
                wordBreak: "break-word",
              }}
            >
              {chunk.content.slice(0, 400)}{chunk.content.length > 400 ? "…" : ""}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
