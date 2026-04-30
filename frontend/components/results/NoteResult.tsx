interface Props {
  data: Record<string, unknown>;
}

export default function NoteResult({ data }: Props) {
  if (data.success === false) {
    return (
      <div className="alert alert-danger small">
        <i className="bi bi-exclamation-triangle me-2" />
        {String(data.error ?? "Failed to create note")}
      </div>
    );
  }
  return (
    <div className="alert alert-success small mb-0 d-flex align-items-center gap-2">
      <i className="bi bi-check-circle-fill" />
      <div>
        Note created
        {data.comment_id && <> — ID <code>{String(data.comment_id)}</code></>}
        {data.note_id && <> — ID <code>{String(data.note_id)}</code></>}
      </div>
    </div>
  );
}
