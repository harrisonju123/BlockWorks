import { useMemo } from "react";
import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import { CardShell } from "../common/CardShell";
import { useTimeseries } from "../../hooks/useStats";
import type { TimeRange } from "../../hooks/useStats";
import { formatTimestamp, formatUSD } from "../../utils/format";

interface Props {
  timeRange: TimeRange;
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
    <div className="bg-gray-900/95 backdrop-blur-sm border border-gray-700 rounded px-3 py-2 text-xs border-l-2 border-l-violet-500">
      <p className="text-gray-400 mb-1">
        {new Date(point.payload.timestamp).toLocaleString()}
      </p>
      <p className="text-violet-300 font-mono">{formatUSD(point.value)}</p>
    </div>
  );
}

export function SpendOverTime({ timeRange }: Props) {
  const { data, isLoading, error } = useTimeseries("cost", timeRange);

  const chartData = useMemo(
    () =>
      data?.data.map((pt) => ({
        timestamp: pt.timestamp,
        label: formatTimestamp(pt.timestamp, timeRange),
        value: pt.value,
      })) ?? [],
    [data, timeRange]
  );

  return (
    <CardShell
      title="Spend over time"
      loading={isLoading}
      error={error}
      skeletonHeight="h-52"
      className="min-h-[16rem]"
    >
      <ResponsiveContainer width="100%" height={200}>
        <AreaChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="spendGradient" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#7c3aed" stopOpacity={0.3} />
              <stop offset="100%" stopColor="#7c3aed" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#111827" strokeOpacity={0.6} />
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
          <Area
            type="monotone"
            dataKey="value"
            stroke="#7c3aed"
            strokeWidth={2}
            fill="url(#spendGradient)"
            dot={false}
            activeDot={{ r: 4, fill: "#7c3aed", stroke: "#1f2937", strokeWidth: 2 }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </CardShell>
  );
}
