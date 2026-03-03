import { useTopTraces } from "../hooks/useStats";
import type { TimeRange } from "../hooks/useStats";
import { modelColor } from "./charts/modelColors";

interface Props {
  timeRange: TimeRange;
}

function truncateId(id: string): string {
  // Show first 8 chars — enough to identify a trace in logs
  return id.slice(0, 8) + "…";
}

function formatMs(ms: number): string {
  if (ms >= 60_000) return `${(ms / 60_000).toFixed(1)}m`;
  if (ms >= 1_000) return `${(ms / 1_000).toFixed(1)}s`;
  return `${ms.toFixed(0)}ms`;
}

export function TopTracesTable({ timeRange }: Props) {
  const { data, isLoading, error } = useTopTraces(timeRange);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4 flex flex-col">
      <h2 className="text-xs font-semibold uppercase tracking-wider text-gray-400 mb-3">
        Top 10 traces by cost
      </h2>

      {isLoading && (
        <div className="space-y-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-6 animate-pulse rounded bg-gray-800" />
          ))}
        </div>
      )}

      {!isLoading && error && (
        <p className="text-xs text-red-400 py-4">Error: {error.message}</p>
      )}

      {!isLoading && !error && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-gray-800 text-gray-500">
                <th className="text-left py-1.5 pr-4 font-medium">Trace ID</th>
                <th className="text-right py-1.5 pr-4 font-medium">Cost</th>
                <th className="text-right py-1.5 pr-4 font-medium">Tokens</th>
                <th className="text-right py-1.5 pr-4 font-medium">Calls</th>
                <th className="text-right py-1.5 pr-4 font-medium">Latency</th>
                <th className="text-left py-1.5 pr-4 font-medium">Models</th>
                <th className="text-left py-1.5 font-medium">Framework</th>
              </tr>
            </thead>
            <tbody>
              {(data?.traces ?? []).length === 0 ? (
                <tr>
                  <td colSpan={7} className="text-center text-gray-600 py-6">
                    No traces yet
                  </td>
                </tr>
              ) : (
                (data?.traces ?? []).map((trace) => (
                  <tr
                    key={trace.trace_id}
                    className="border-b border-gray-800/50 hover:bg-gray-800/30 transition-colors"
                  >
                    <td className="py-1.5 pr-4 font-mono text-gray-300">
                      {truncateId(trace.trace_id)}
                    </td>
                    <td className="py-1.5 pr-4 font-mono text-right text-violet-300">
                      ${trace.total_cost_usd.toFixed(4)}
                    </td>
                    <td className="py-1.5 pr-4 font-mono text-right text-gray-300">
                      {trace.total_tokens.toLocaleString()}
                    </td>
                    <td className="py-1.5 pr-4 font-mono text-right text-gray-300">
                      {trace.event_count}
                    </td>
                    <td className="py-1.5 pr-4 font-mono text-right text-gray-400">
                      {formatMs(trace.total_latency_ms)}
                    </td>
                    <td className="py-1.5 pr-4">
                      <div className="flex gap-1 flex-wrap">
                        {trace.models_used.map((m) => (
                          <span
                            key={m}
                            className="px-1.5 py-0.5 rounded text-[10px] font-mono"
                            style={{
                              backgroundColor: modelColor(m) + "22",
                              color: modelColor(m),
                              border: `1px solid ${modelColor(m)}44`,
                            }}
                          >
                            {m.length > 18 ? m.slice(0, 16) + "…" : m}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td className="py-1.5 text-gray-500">
                      {trace.agent_framework ?? "–"}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
