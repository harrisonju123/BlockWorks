import { CardShell } from "../common/CardShell";
import { useWasteScore } from "../../hooks/useStats";
import type { TimeRange } from "../../hooks/useStats";

interface Props {
  timeRange: TimeRange;
}

// Color thresholds match the spec: green < 20%, yellow 20–50%, red > 50%
function scoreColor(score: number): { bar: string; text: string } {
  if (score < 0.2) return { bar: "#16a34a", text: "text-green-400" };
  if (score < 0.5) return { bar: "#d97706", text: "text-amber-400" };
  return { bar: "#dc2626", text: "text-red-400" };
}

function scoreLabel(score: number): string {
  if (score < 0.2) return "Low waste";
  if (score < 0.5) return "Moderate waste";
  return "High waste";
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
        {/* Big number */}
        <div className="flex items-baseline gap-2">
          <span className={`text-4xl font-mono font-semibold ${colors.text}`}>
            {pct.toFixed(0)}%
          </span>
          <span className="text-xs text-gray-500">{scoreLabel(data?.waste_score ?? 0)}</span>
        </div>

        {/* Progress bar */}
        <div className="w-full h-2 bg-gray-800 rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{ width: `${Math.min(pct, 100)}%`, backgroundColor: colors.bar }}
          />
        </div>

        {/* Potential savings */}
        <div className="text-xs text-gray-400">
          Potential savings:{" "}
          <span className="font-mono text-gray-200">
            ${(data?.total_potential_savings_usd ?? 0).toFixed(2)}
          </span>
        </div>

        {/* Breakdown table — only rendered when there are items */}
        {(data?.breakdown ?? []).length > 0 && (
          <div className="mt-1 border-t border-gray-800 pt-3">
            <p className="text-xs text-gray-500 mb-2 uppercase tracking-wider">
              Top suggestions
            </p>
            <ul className="space-y-1.5">
              {data!.breakdown.slice(0, 4).map((item, i) => (
                <li key={i} className="flex justify-between text-xs">
                  <span className="text-gray-400 truncate max-w-[55%]">
                    {item.current_model}
                    <span className="text-gray-600 mx-1">→</span>
                    {item.suggested_model}
                  </span>
                  <span className="font-mono text-green-400">
                    save ${item.savings_usd.toFixed(2)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </CardShell>
  );
}
