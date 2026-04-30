interface ToolCardProps {
  icon: string;
  iconBg: string;
  title: string;
  description: string;
  badge?: string;
  badgeColor?: string;
  onClick?: () => void;
  disabled?: boolean;
}

export default function ToolCard({
  icon,
  iconBg,
  title,
  description,
  badge,
  badgeColor = "secondary",
  onClick,
  disabled = false,
}: ToolCardProps) {
  return (
    <div
      className={`tool-card h-100${disabled ? " opacity-50" : ""}`}
      onClick={disabled ? undefined : onClick}
      style={disabled ? { cursor: "not-allowed" } : undefined}
    >
      <div className="tool-icon" style={{ background: iconBg }}>
        <i className={`bi ${icon} text-white`} />
      </div>
      <div className="d-flex align-items-start justify-content-between">
        <div className="fw-semibold" style={{ fontSize: "0.9rem" }}>
          {title}
        </div>
        {badge && (
          <span className={`badge bg-${badgeColor} ms-2 flex-shrink-0`} style={{ fontSize: "0.65rem" }}>
            {badge}
          </span>
        )}
      </div>
      <div className="text-muted mt-1" style={{ fontSize: "0.78rem" }}>
        {description}
      </div>
    </div>
  );
}
