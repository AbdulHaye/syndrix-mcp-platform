interface Lead {
  item_id?: unknown;
  id?: unknown;
  title?: unknown;
  name?: unknown;
  email?: unknown;
  company?: unknown;
  stage?: unknown;
  [key: string]: unknown;
}

interface Props {
  data: { query: string; total: number; results: Lead[] };
}

export default function LeadTable({ data }: Props) {
  if (!data.results?.length) {
    return (
      <div className="text-center py-4 text-muted small">
        <i className="bi bi-search me-2" />
        No leads found for &quot;{data.query}&quot;
      </div>
    );
  }

  return (
    <div>
      <div className="d-flex align-items-center justify-content-between mb-2">
        <span className="small text-muted">
          {data.total} result{data.total !== 1 ? "s" : ""} for &quot;{data.query}&quot;
        </span>
      </div>
      <div className="table-responsive border rounded-3 overflow-hidden">
        <table className="table table-hover mb-0 small">
          <thead className="table-light">
            <tr>
              <th>Name / Title</th>
              <th>Email</th>
              <th>Company</th>
              <th>Stage</th>
            </tr>
          </thead>
          <tbody>
            {data.results.map((lead, i) => (
              <tr key={String(lead.item_id ?? lead.id ?? i)}>
                <td className="fw-semibold">
                  {String(lead.title ?? lead.name ?? `Lead ${i + 1}`)}
                </td>
                <td className="text-muted">{String(lead.email ?? "—")}</td>
                <td>{String(lead.company ?? "—")}</td>
                <td>
                  {lead.stage ? (
                    <span className="badge bg-success-subtle text-success border border-success-subtle">
                      {String(lead.stage)}
                    </span>
                  ) : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
