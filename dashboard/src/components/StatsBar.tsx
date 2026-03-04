import { useSummary, usePreviousSummary } from "../hooks/useStats";
import type { TimeRange } from "../hooks/useStats";
import { formatUSD } from "../utils/format";
import { InfoTip } from "./common/InfoTip";

interface Props {
  timeRange: TimeRange;
}

type Sentiment = "positive" | "negative" | "neutral";

interface StatCardProps {
  label: string;
  value: string;
  loading: boolean;
  delta?: number | null;
  /** Whether "up" is good or bad for this metric */
  upIsGood?: boolean;
  /** Conditional threshold coloring */
  valueClassName?: string;
  tip?: string;
}

function formatDelta(delta: number): string {
  const abs = Math.abs(delta);
  if (abs >= 100) return `${abs.toFixed(0)}%`;
  return `${abs.toFixed(1)}%`;
}

function deltaSentiment(delta: number, upIsGood: boolean): Sentiment {
  if (Math.abs(delta) < 0.5) return "neutral";
  const isUp = delta > 0;
  return (isUp === upIsGood) ? "positive" : "negative";
}

const SENTIMENT_COLORS: Record<Sentiment, string> = {
  positive: "text-green-400",
  negative: "text-red-400",
  neutral: "text-gray-500",
};

function StatCard({ label, value, loading, delta, upIsGood = true, valueClassName, tip }: StatCardProps) {
  const sentiment = delta != null ? deltaSentiment(delta, upIsGood) : "neutral";
  const arrow = delta != null && Math.abs(delta) >= 0.5
    ? (delta > 0 ? "▲" : "▼")
    : null;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg px-4 py-3 flex flex-col gap-1">
      {loading ? (
        <div className="h-8 w-24 animate-pulse rounded bg-gray-800" />
      ) : (
        <div className="flex items-baseline gap-2">
          <span className={`text-2xl font-mono font-semibold ${valueClassName ?? "text-gray-100"}`}>
            {value}
          </span>
          {arrow && delta != null && (
            <span className={`text-xs font-mono ${SENTIMENT_COLORS[sentiment]}`}>
              {arrow} {formatDelta(delta)}
            </span>
          )}
        </div>
      )}
      <span className="text-xs uppercase tracking-wider text-gray-500 flex items-center gap-1">{label} <InfoTip text={tip} /></span>
    </div>
  );
}

function valueColor(metric: string, rawValue: number): string | undefined {
  if (metric === "failure") {
    if (rawValue > 0.10) return "text-red-400";
    if (rawValue > 0.05) return "text-amber-400";
  }
  if (metric === "latency") {
    if (rawValue > 5000) return "text-red-400";
    if (rawValue > 2000) return "text-amber-400";
  }
  return undefined;
}

export function StatsBar({ timeRange }: Props) {
  const { data, isLoading } = useSummary(timeRange);
  const { data: prevData } = usePreviousSummary(timeRange);

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

  const prevAvgLatency = (() => {
    if (!prevData?.groups.length) return 0;
    const totalRequests = prevData.groups.reduce((s, g) => s + g.request_count, 0);
    if (totalRequests === 0) return 0;
    const weightedSum = prevData.groups.reduce(
      (s, g) => s + g.avg_latency_ms * g.request_count,
      0
    );
    return weightedSum / totalRequests;
  })();

  // Compute % change; null if no previous data
  function pctChange(current: number, previous: number): number | null {
    if (!prevData || previous === 0) return null;
    return ((current - previous) / previous) * 100;
  }

  const cards = [
    {
      label: "Total requests",
      value: isLoading ? "–" : (data?.total_requests ?? 0).toLocaleString(),
      delta: pctChange(data?.total_requests ?? 0, prevData?.total_requests ?? 0),
      upIsGood: true,
    },
    {
      label: "Total cost",
      value: isLoading ? "–" : formatUSD(data?.total_cost_usd ?? 0),
      delta: pctChange(data?.total_cost_usd ?? 0, prevData?.total_cost_usd ?? 0),
      upIsGood: false,
    },
    {
      label: "Avg latency",
      value: isLoading ? "–" : `${avgLatency.toFixed(0)} ms`,
      delta: pctChange(avgLatency, prevAvgLatency),
      upIsGood: false,
      valueClassName: valueColor("latency", avgLatency),
      tip: "Request-weighted mean latency across all models. High values may indicate throttling or oversized prompts.",
    },
    {
      label: "Failure rate",
      value: isLoading
        ? "–"
        : `${((data?.failure_rate ?? 0) * 100).toFixed(1)}%`,
      delta: pctChange(data?.failure_rate ?? 0, prevData?.failure_rate ?? 0),
      upIsGood: false,
      valueClassName: valueColor("failure", data?.failure_rate ?? 0),
      tip: "Percentage of LLM calls that returned an error or timed out.",
    },
  ];

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
      {cards.map((c) => (
        <StatCard
          key={c.label}
          label={c.label}
          value={c.value}
          loading={isLoading}
          delta={c.delta}
          upIsGood={c.upIsGood}
          valueClassName={c.valueClassName}
          tip={c.tip}
        />
      ))}
    </div>
  );
}
