import { useMemo } from "react";
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
import { formatUSD } from "../../utils/format";

interface Props {
  timeRange: TimeRange;
}

interface ChartEntry {
  key: string;
  value: number;
}

interface TooltipPayload {
  value: number;
  payload: ChartEntry;
}

function sanitizeId(s: string): string {
  return s.replace(/[^a-zA-Z0-9]/g, "-");
}

function CustomTooltip({
  active,
  payload,
  totalSpend,
}: {
  active?: boolean;
  payload?: TooltipPayload[];
  totalSpend: number;
}) {
  if (!active || !payload?.length) return null;
  const item = payload[0];
  const pct = totalSpend > 0 ? (item.value / totalSpend) * 100 : 0;
  return (
    <div className="bg-gray-900/95 backdrop-blur-sm border border-gray-700 rounded px-3 py-2 text-xs">
      <p className="text-gray-300 font-mono mb-1">{item.payload.key}</p>
      <p className="font-mono" style={{ color: modelColor(item.payload.key) }}>
        {formatUSD(item.value)}
      </p>
      <p className="text-gray-500 mt-0.5">{pct.toFixed(1)}% of total spend</p>
    </div>
  );
}

export function CostDistribution({ timeRange }: Props) {
  const { data, isLoading, error } = useSummary(timeRange, "model");

  const chartData = useMemo(
    () =>
      [...(data?.groups ?? [])]
        .sort((a, b) => b.total_cost_usd - a.total_cost_usd)
        .map((g) => ({ key: g.key, value: g.total_cost_usd })),
    [data]
  );

  const totalSpend = useMemo(
    () => chartData.reduce((sum, d) => sum + d.value, 0),
    [chartData]
  );

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
            <defs>
              {chartData.map((entry) => {
                const color = modelColor(entry.key);
                const id = `bar-${sanitizeId(entry.key)}`;
                return (
                  <linearGradient key={id} id={id} x1="0" y1="0" x2="1" y2="0">
                    <stop offset="0%" stopColor={color} stopOpacity={0.6} />
                    <stop offset="100%" stopColor={color} stopOpacity={1} />
                  </linearGradient>
                );
              })}
            </defs>
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
            <Tooltip
              content={<CustomTooltip totalSpend={totalSpend} />}
              cursor={{ fill: "#1f2937" }}
            />
            <Bar dataKey="value" radius={[0, 2, 2, 0]} maxBarSize={18}>
              {chartData.map((entry) => (
                <Cell
                  key={entry.key}
                  fill={`url(#bar-${sanitizeId(entry.key)})`}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </CardShell>
  );
}
