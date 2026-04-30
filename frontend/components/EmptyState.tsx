interface EmptyStateProps {
  icon?: string;
  title: string;
  description?: string;
  children?: React.ReactNode;
}

export default function EmptyState({ icon = "bi-inbox", title, description, children }: EmptyStateProps) {
  return (
    <div className="empty-state">
      <i className={`bi ${icon}`} />
      <div className="fw-semibold text-secondary mb-1">{title}</div>
      {description && <p className="small mb-3">{description}</p>}
      {children}
    </div>
  );
}
