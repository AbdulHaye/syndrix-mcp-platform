interface SkeletonProps {
  width?: string;
  height?: string;
  rounded?: boolean;
  className?: string;
}

export function Skeleton({ width = "100%", height = "1rem", rounded = false, className = "" }: SkeletonProps) {
  return (
    <div
      className={`skeleton ${rounded ? "rounded-circle" : "rounded"} ${className}`}
      style={{ width, height }}
    />
  );
}

export function SkeletonCard() {
  return (
    <div className="bg-white rounded-3 border p-4">
      <Skeleton height="1.1rem" width="40%" className="mb-3" />
      <Skeleton height="0.85rem" className="mb-2" />
      <Skeleton height="0.85rem" width="80%" className="mb-2" />
      <Skeleton height="0.85rem" width="60%" />
    </div>
  );
}

export function SkeletonTable({ rows = 4, cols = 3 }: { rows?: number; cols?: number }) {
  return (
    <div className="bg-white rounded-3 border p-3">
      <Skeleton height="1rem" width="30%" className="mb-3" />
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="d-flex gap-3 mb-2">
          {Array.from({ length: cols }).map((__, j) => (
            <Skeleton key={j} height="0.85rem" width={`${Math.round(100 / cols)}%`} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function SkeletonText({ lines = 6 }: { lines?: number }) {
  return (
    <div className="bg-white rounded-3 border p-4">
      <Skeleton height="1rem" width="30%" className="mb-3" />
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          height="0.85rem"
          width={i === lines - 1 ? "60%" : "100%"}
          className="mb-2"
        />
      ))}
    </div>
  );
}
