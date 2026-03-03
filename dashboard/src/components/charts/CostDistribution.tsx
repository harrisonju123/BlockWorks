import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
  ResponsiveContainer,
} from "recharts";
import { CardShell } from "../common/CardShell";
import { useSummary } from "../../hooks/useStats";
import type { TimeRange } from "../../hooks/useStats";
import { modelColor } from "./modelColors";

interface Props {
  timeRange: TimeRange;
}

function formatUSD(value: number): string {
  if (value >= 1) return `$${value.toFixed(2)}`;
  return `$${value.toFixed(4)}`;
}

interface TooltipPayload {
  value: number;
  payload: { key: string };
}

function CustomTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: TooltipPayload[];
}) {
  if (!active || !payload?.length) return null;
  const item = payload[0];
  return (
    <div className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-xs">
      <p className="text-gray-300 font-mono mb-1">{item.payload.key}</p>
      <p className="font-mono" style={{ color: modelColor(item.payload.key) }}>
        {formatUSD(item.value)}
      </p>
    </div>
  );
}

export function CostDistribution({ timeRange }: Props) {
  const { data, isLoading, error } = useSummary(timeRange, "model");

  // Sort descending by cost so the biggest spenders appear at top
  const chartData = [...(data?.groups ?? [])]
    .sort((a, b) => b.total_cost_usd - a.total_cost_usd)
    .map((g) => ({ key: g.key, value: g.total_cost_usd }));

  // Dynamic height: at least 160px, grows with number of bars (28px each)
  const chartHeight = Math.max(160, chartData.length * 28 + 16);

  return (
    <CardShell
      title="Cost by model"
      loading={isLoading}
      error={error}
      skeletonHeight="h-40"
      className="min-h-[12rem]"
    >
      {chartData.length === 0 ? (
        <p className="text-xs text-gray-500 py-6 text-center">No data</p>
      ) : (
        <ResponsiveContainer width="100%" height={chartHeight}>
          <BarChart
            layout="vertical"
            data={chartData}
            margin={{ top: 0, right: 8, left: 0, bottom: 0 }}
          >
            <XAxis
              type="number"
              tickFormatter={formatUSD}
              tick={{ fontSize: 10, fill: "#6b7280" }}
              tickLine={false}
              axisLine={{ stroke: "#374151" }}
            />
            <YAxis
              type="category"
              dataKey="key"
              width={120}
              tick={{ fontSize: 10, fill: "#9ca3af" }}
              tickLine={false}
              axisLine={false}
            />
            <Tooltip content={<CustomTooltip />} cursor={{ fill: "#1f2937" }} />
            <Bar dataKey="value" radius={[0, 2, 2, 0]} maxBarSize={18}>
              {chartData.map((entry) => (
                <Cell key={entry.key} fill={modelColor(entry.key)} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </CardShell>
  );
}
