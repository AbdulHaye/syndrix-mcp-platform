interface PR {
  number: number;
  title: string;
  author: string;
  created_at: string;
  html_url: string;
}

interface Props {
  data: { repo: string; total: number; pull_requests: PR[] };
}

export default function PRTable({ data }: Props) {
  if (!data.pull_requests?.length) {
    return (
      <div className="text-center py-4 text-muted small">
        <i className="bi bi-git me-2" />
        No open pull requests for <code>{data.repo}</code>
      </div>
    );
  }

  return (
    <div>
      <div className="d-flex align-items-center justify-content-between mb-2">
        <span className="small text-muted">
          {data.total} open PR{data.total !== 1 ? "s" : ""} in <code>{data.repo}</code>
        </span>
      </div>
      <div className="d-flex flex-column gap-2">
        {data.pull_requests.map((pr) => (
          <a
            key={pr.number}
            href={pr.html_url}
            target="_blank"
            rel="noreferrer"
            className="text-decoration-none"
          >
            <div className="border rounded-3 p-3 bg-white d-flex align-items-center gap-3">
              <span
                className="badge bg-success-subtle text-success border border-success-subtle flex-shrink-0"
                style={{ fontSize: "0.7rem" }}
              >
                #{pr.number}
              </span>
              <div className="flex-grow-1 min-width-0">
                <div className="fw-semibold text-dark" style={{ fontSize: "0.9rem" }}>
                  {pr.title}
                </div>
                <div className="small text-muted mt-1">
                  <i className="bi bi-person me-1" />{pr.author}
                  <span className="mx-2">·</span>
                  <i className="bi bi-clock me-1" />
                  {new Date(pr.created_at).toLocaleDateString()}
                </div>
              </div>
              <i className="bi bi-box-arrow-up-right text-muted flex-shrink-0" />
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
