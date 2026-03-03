import { CardShell } from "../common/CardShell";
import { useWasteScore } from "../../hooks/useStats";
import type { TimeRange } from "../../hooks/useStats";

interface Props {
  timeRange: TimeRange;
}

// Color thresholds: green < 20%, amber 20–50%, red > 50%
function scoreColor(score: number): { ring: string; text: string } {
  if (score < 0.2) return { ring: "#16a34a", text: "text-green-400" };
  if (score < 0.5) return { ring: "#d97706", text: "text-amber-400" };
  return { ring: "#dc2626", text: "text-red-400" };
}

function scoreLabel(score: number): string {
  if (score < 0.2) return "Low waste";
  if (score < 0.5) return "Moderate waste";
  return "High waste";
}

function confidenceBadge(confidence: number): { label: string; className: string } {
  if (confidence >= 0.8) return { label: "high", className: "bg-green-900/40 text-green-400 border-green-700/40" };
  if (confidence >= 0.5) return { label: "med", className: "bg-amber-900/40 text-amber-400 border-amber-700/40" };
  return { label: "low", className: "bg-gray-800 text-gray-400 border-gray-700" };
}

interface RingGaugeProps {
  value: number; // 0–1
  color: string;
  size?: number;
}

function RingGauge({ value, color, size = 120 }: RingGaugeProps) {
  const strokeWidth = 8;
  const radius = (size - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const offset = circumference * (1 - Math.min(value, 1));

  return (
    <svg width={size} height={size} className="block mx-auto">
      {/* Track */}
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke="#1f2937"
        strokeWidth={strokeWidth}
      />
      {/* Filled arc */}
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeDasharray={circumference}
        strokeDashoffset={offset}
        className="transition-all duration-700"
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
    </svg>
  );
}

export function WasteScore({ timeRange }: Props) {
  const { data, isLoading, error } = useWasteScore(timeRange);

  const pct = data ? data.waste_score * 100 : 0;
  const colors = scoreColor(data?.waste_score ?? 0);

  return (
    <CardShell
      title="Waste score"
      loading={isLoading}
      error={error}
      skeletonHeight="h-32"
    >
      <div className="flex flex-col gap-4">
        {/* Ring gauge with centered label */}
        <div className="relative">
          <RingGauge value={data?.waste_score ?? 0} color={colors.ring} />
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className={`text-3xl font-mono font-semibold ${colors.text}`}>
              {pct.toFixed(0)}%
            </span>
            <span className="text-[10px] text-gray-500">
              {scoreLabel(data?.waste_score ?? 0)}
            </span>
          </div>
        </div>

        {/* Potential savings */}
        <div className="text-xs text-gray-400 text-center">
          Potential savings:{" "}
          <span className="font-mono text-gray-200">
            ${(data?.total_potential_savings_usd ?? 0).toFixed(2)}
          </span>
        </div>

        {/* Breakdown with confidence badges */}
        {(data?.breakdown ?? []).length > 0 && (
          <div className="border-t border-gray-800 pt-3">
            <p className="text-xs text-gray-500 mb-2 uppercase tracking-wider">
              Top suggestions
            </p>
            <ul className="space-y-1.5">
              {data!.breakdown.slice(0, 4).map((item, i) => {
                const badge = confidenceBadge(item.confidence);
                return (
                  <li key={i} className="flex items-center justify-between text-xs gap-2">
                    <span className="text-gray-400 truncate flex-1 min-w-0">
                      {item.current_model}
                      <span className="text-gray-600 mx-1">→</span>
                      {item.suggested_model}
                    </span>
                    <span
                      className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] border ${badge.className}`}
                    >
                      {badge.label}
                    </span>
                    <span className="font-mono text-green-400 shrink-0">
                      save ${item.savings_usd.toFixed(2)}
                    </span>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>
    </CardShell>
  );
}
