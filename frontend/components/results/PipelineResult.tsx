interface Stage {
  id?: unknown;
  name?: unknown;
  [key: string]: unknown;
}

interface Props {
  data: { success?: boolean; stages?: Stage[]; error?: string };
}

export default function PipelineResult({ data }: Props) {
  if (data.success === false) {
    return (
      <div className="alert alert-danger small">
        <i className="bi bi-exclamation-triangle me-2" />
        {String(data.error ?? "Failed")}
      </div>
    );
  }

  const stages = data.stages ?? [];
  if (!stages.length) {
    return <div className="text-muted small text-center py-3">No pipeline stages found</div>;
  }

  return (
    <div className="d-flex flex-wrap gap-2">
      {stages.map((s, i) => (
        <div key={String(s.id ?? i)} className="badge fs-6 bg-primary-subtle text-primary border border-primary-subtle px-3 py-2">
          <i className="bi bi-diagram-3 me-2" />
          {String(s.name ?? `Stage ${i + 1}`)}
        </div>
      ))}
    </div>
  );
}
