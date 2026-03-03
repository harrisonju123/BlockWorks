import { useSummary } from "../hooks/useStats";
import type { TimeRange } from "../hooks/useStats";

interface Props {
  timeRange: TimeRange;
}

interface StatCardProps {
  label: string;
  value: string;
  loading: boolean;
}

function StatCard({ label, value, loading }: StatCardProps) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg px-4 py-3 flex flex-col gap-1 flex-1 min-w-[140px]">
      {loading ? (
        <div className="h-8 w-24 animate-pulse rounded bg-gray-800" />
      ) : (
        <span className="text-2xl font-mono font-semibold text-gray-100">{value}</span>
      )}
      <span className="text-xs uppercase tracking-wider text-gray-500">{label}</span>
    </div>
  );
}

export function StatsBar({ timeRange }: Props) {
  const { data, isLoading } = useSummary(timeRange);

  // Avg latency is derived from group data — weighted average across all groups
  const avgLatency = (() => {
    if (!data?.groups.length) return 0;
    const totalRequests = data.groups.reduce((s, g) => s + g.request_count, 0);
    if (totalRequests === 0) return 0;
    const weightedSum = data.groups.reduce(
      (s, g) => s + g.avg_latency_ms * g.request_count,
      0
    );
    return weightedSum / totalRequests;
  })();

  const cards = [
    {
      label: "Total requests",
      value: isLoading ? "–" : (data?.total_requests ?? 0).toLocaleString(),
    },
    {
      label: "Total cost",
      value: isLoading ? "–" : `$${(data?.total_cost_usd ?? 0).toFixed(4)}`,
    },
    {
      label: "Avg latency",
      value: isLoading ? "–" : `${avgLatency.toFixed(0)} ms`,
    },
    {
      label: "Failure rate",
      value: isLoading
        ? "–"
        : `${((data?.failure_rate ?? 0) * 100).toFixed(1)}%`,
    },
  ];

  return (
    <div className="flex gap-4 flex-wrap">
      {cards.map((c) => (
        <StatCard key={c.label} label={c.label} value={c.value} loading={isLoading} />
      ))}
    </div>
  );
}
