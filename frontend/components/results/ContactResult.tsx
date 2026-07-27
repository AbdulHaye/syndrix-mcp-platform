interface Props {
  data: Record<string, unknown>;
}

function Field({ label, value }: { label: string; value?: unknown }) {
  if (!value) return null;
  return (
    <div className="col-sm-6 mb-2">
      <div className="small text-muted">{label}</div>
      <div className="fw-semibold" style={{ fontSize: "0.9rem" }}>{String(value)}</div>
    </div>
  );
}

export default function ContactResult({ data }: Props) {
  // Handle error response
  if (data.success === false) {
    return (
      <div className="alert alert-danger small">
        <i className="bi bi-exclamation-triangle me-2" />
        {String(data.error ?? "Failed to fetch contact")}
      </div>
    );
  }

  const name = String(data.name ?? data.title ?? data.item_id ?? "Contact");
  const initials = name.split(" ").map((w: string) => w[0]).join("").slice(0, 2).toUpperCase();

  return (
    <div className="border rounded-3 p-4 bg-white">
      <div className="d-flex align-items-center gap-3 mb-3 pb-3 border-bottom">
        <div
          style={{
            width: 48, height: 48, borderRadius: "50%",
            background: "#0d6efd", color: "#fff",
            display: "flex", alignItems: "center", justifyContent: "center",
            fontWeight: 700, fontSize: "1rem", flexShrink: 0,
          }}
        >
          {initials || "?"}
        </div>
        <div>
          <div className="fw-bold">{name}</div>
          {Boolean(data.email) && <div className="small text-muted">{String(data.email)}</div>}
        </div>
        {Boolean(data.stage) && (
          <span className="badge bg-success ms-auto">{String(data.stage)}</span>
        )}
      </div>
      <div className="row">
        <Field label="Phone"   value={data.phone} />
        <Field label="Company" value={data.company} />
        <Field label="Title"   value={data.title} />
        <Field label="Source"  value={data.source} />
        <Field label="Last Contacted" value={data.last_contacted} />
        {Array.isArray(data.tags) && (
          <div className="col-12 mt-1">
            <div className="small text-muted mb-1">Tags</div>
            <div className="d-flex flex-wrap gap-1">
              {(data.tags as string[]).map((t) => (
                <span key={t} className="badge bg-secondary">{t}</span>
              ))}
            </div>
          </div>
        )}
      </div>
      {Boolean(data.note) && (
        <div className="alert alert-info small mt-2 mb-0 py-2">
          <i className="bi bi-info-circle me-1" />{String(data.note)}
        </div>
      )}
    </div>
  );
}
