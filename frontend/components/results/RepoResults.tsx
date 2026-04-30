interface Repo {
  full_name: string;
  description?: string;
  html_url: string;
  stars: number;
  language?: string;
}

interface Props {
  data: { query: string; total_count: number; results: Repo[] };
}

const LANG_COLORS: Record<string, string> = {
  Python: "#3572A5", TypeScript: "#2b7489", JavaScript: "#f1e05a",
  Go: "#00ADD8", Rust: "#dea584", Java: "#b07219", "C#": "#178600",
  Ruby: "#701516", PHP: "#4F5D95",
};

export default function RepoResults({ data }: Props) {
  if (!data.results?.length) {
    return (
      <div className="text-center py-4 text-muted small">
        <i className="bi bi-github me-2" />
        No repositories found for &quot;{data.query}&quot;
      </div>
    );
  }

  return (
    <div>
      <div className="small text-muted mb-2">
        {data.total_count.toLocaleString()} repositories found
      </div>
      <div className="d-flex flex-column gap-2">
        {data.results.map((repo) => (
          <a
            key={repo.full_name}
            href={repo.html_url}
            target="_blank"
            rel="noreferrer"
            className="text-decoration-none"
          >
            <div className="border rounded-3 p-3 bg-white hover-shadow">
              <div className="d-flex align-items-start justify-content-between gap-2">
                <div className="fw-semibold text-primary" style={{ fontSize: "0.9rem" }}>
                  <i className="bi bi-github me-2 text-dark" />
                  {repo.full_name}
                </div>
                <div className="d-flex align-items-center gap-1 text-muted small flex-shrink-0">
                  <i className="bi bi-star" />
                  {repo.stars.toLocaleString()}
                </div>
              </div>
              {repo.description && (
                <div className="text-muted small mt-1">{repo.description}</div>
              )}
              {repo.language && (
                <div className="mt-2 d-flex align-items-center gap-1">
                  <span
                    style={{
                      width: 10, height: 10, borderRadius: "50%",
                      background: LANG_COLORS[repo.language] ?? "#8a8a8a",
                      display: "inline-block",
                    }}
                  />
                  <span className="small text-muted">{repo.language}</span>
                </div>
              )}
            </div>
          </a>
        ))}
      </div>
    </div>
  );
}
