import {
  BarChart,
  Bar,
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
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
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
      <p className="text-emerald-300 font-mono">
        {point.value.toLocaleString()} requests
      </p>
    </div>
  );
}

export function RequestsOverTime({ timeRange }: Props) {
  const { data, isLoading, error } = useTimeseries("requests", timeRange);

  const chartData = data?.data.map((pt) => ({
    timestamp: pt.timestamp,
    label: formatTimestamp(pt.timestamp, timeRange),
    value: pt.value,
  })) ?? [];

  return (
    <CardShell
      title="Requests over time"
      loading={isLoading}
      error={error}
      skeletonHeight="h-52"
      className="min-h-[16rem]"
    >
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fontSize: 10, fill: "#6b7280" }}
            tickLine={false}
            axisLine={{ stroke: "#374151" }}
            interval="preserveStartEnd"
          />
          <YAxis
            tick={{ fontSize: 10, fill: "#6b7280" }}
            tickLine={false}
            axisLine={false}
            width={40}
          />
          <Tooltip content={<CustomTooltip />} cursor={{ fill: "#1f2937" }} />
          <Bar dataKey="value" fill="#16a34a" radius={[2, 2, 0, 0]} maxBarSize={24} />
        </BarChart>
      </ResponsiveContainer>
    </CardShell>
  );
}
