import type { ReactNode } from "react";

interface CardShellProps {
  title?: string;
  loading?: boolean;
  error?: Error | null;
  /** Skeleton height when loading, e.g. "h-48" */
  skeletonHeight?: string;
  children: ReactNode;
  className?: string;
}

/** Consistent card wrapper used by every chart/widget on the dashboard. */
export function CardShell({
  title,
  loading,
  error,
  skeletonHeight = "h-48",
  children,
  className = "",
}: CardShellProps) {
  return (
    <div className={`bg-gray-900 border border-gray-800 rounded-lg p-4 flex flex-col ${className}`}>
      {title && (
        <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-3">
          {title}
        </h2>
      )}

      {loading && (
        <div className={`${skeletonHeight} w-full animate-pulse rounded bg-gray-800`} />
      )}

      {!loading && error && (
        <div className="flex items-center justify-center text-red-400 text-xs py-6">
          Error: {error.message}
        </div>
      )}

      {!loading && !error && children}
    </div>
  );
}
