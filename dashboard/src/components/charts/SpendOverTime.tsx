import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { CardShell } from "../common/CardShell";
import { useTimeseries } from "../../hooks/useStats";
import type { TimeRange } from "../../hooks/useStats";

interface Props {
  timeRange: TimeRange;
}

function formatTimestamp(iso: string, timeRange: TimeRange): string {
  const d = new Date(iso);
  if (timeRange === "30d") {
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  }
  if (timeRange === "7d") {
    return d.toLocaleDateString("en-US", { weekday: "short", hour: "numeric" });
  }
  // 24h: show just the hour
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function formatUSD(value: number): string {
  if (value >= 1) return `$${value.toFixed(2)}`;
  // Sub-dollar amounts shown with more precision
  return `$${value.toFixed(4)}`;
}

interface TooltipPayload {
  value: number;
  payload: { timestamp: string };
}

function CustomTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: TooltipPayload[];
}) {
  if (!active || !payload?.length) return null;
  const point = payload[0];
  return (
    <div className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-xs">
      <p className="text-gray-400 mb-1">
        {new Date(point.payload.timestamp).toLocaleString()}
      </p>
      <p className="text-violet-300 font-mono">{formatUSD(point.value)}</p>
    </div>
  );
}

export function SpendOverTime({ timeRange }: Props) {
  const { data, isLoading, error } = useTimeseries("cost", timeRange);

  const chartData = data?.data.map((pt) => ({
    timestamp: pt.timestamp,
    // Label shown on axis — derived from full timestamp
    label: formatTimestamp(pt.timestamp, timeRange),
    value: pt.value,
  })) ?? [];

  return (
    <CardShell
      title="Spend over time"
      loading={isLoading}
      error={error}
      skeletonHeight="h-52"
      className="min-h-[16rem]"
    >
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 10, fill: "#6b7280" }}
            tickLine={false}
            axisLine={{ stroke: "#374151" }}
            interval="preserveStartEnd"
          />
          <YAxis
            tickFormatter={formatUSD}
            tick={{ fontSize: 10, fill: "#6b7280" }}
            tickLine={false}
            axisLine={false}
            width={56}
          />
          <Tooltip content={<CustomTooltip />} />
          <Line
            type="monotone"
            dataKey="value"
            stroke="#7c3aed"
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 4, fill: "#7c3aed", stroke: "#1f2937", strokeWidth: 2 }}
          />
        </LineChart>
      </ResponsiveContainer>
    </CardShell>
  );
}
