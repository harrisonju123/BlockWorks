import { useMemo } from "react";
import type { TimeRange } from "../hooks/useStats";
import type { FitnessEntry } from "../api/types";
import {
  useFitnessMatrix,
  useBenchmarkResults,
  useBenchmarkDrift,
  useBenchmarkConfig,
} from "../hooks/useBenchmarks";
import { CardShell } from "../components/common/CardShell";
import { formatUSD, formatMs } from "../utils/format";

interface Props {
  timeRange: TimeRange;
}

export function Benchmarks({ timeRange }: Props) {
  return (
    <div className="flex flex-col gap-6 max-w-[1600px] mx-auto">
      <h1 className="text-lg font-semibold">Benchmarks</h1>

      {/* Config summary */}
      <ConfigBanner />

      {/* Fitness Matrix */}
      <FitnessMatrix timeRange={timeRange} />

      {/* Two-column: drift + recent results */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <DriftDetection timeRange={timeRange} />
        <RecentResults timeRange={timeRange} />
      </div>
    </div>
  );
}

// -- Config Banner ------------------------------------------------------------

function ConfigBanner() {
  const { data: config, isLoading } = useBenchmarkConfig();

  if (isLoading || !config) return null;

  return (
    <div className="flex flex-wrap gap-4 bg-gray-800/50 rounded-lg px-4 py-3">
      <Stat label="Status" value={config.enabled ? "Active" : "Disabled"}
        className={config.enabled ? "text-green-400" : "text-gray-500"} />
      <Stat label="Sample Rate" value={`${(config.sample_rate * 100).toFixed(0)}%`} />
      <Stat label="Models" value={config.benchmark_models.join(", ")} />
      <Stat label="Judge" value={config.judge_model} />
    </div>
  );
}

function Stat({ label, value, className = "text-gray-200" }: { label: string; value: string; className?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] text-gray-500 uppercase">{label}</span>
      <span className={`text-xs font-mono ${className}`}>{value}</span>
    </div>
  );
}

// -- Fitness Matrix -----------------------------------------------------------

function FitnessMatrix({ timeRange }: { timeRange: TimeRange }) {
  const { data, isLoading, error } = useFitnessMatrix(timeRange);

  // Group entries into a model x task_type grid
  const { models, taskTypes, grid } = useMemo(() => {
    if (!data?.entries) return { models: [] as string[], taskTypes: [] as string[], grid: new Map<string, FitnessEntry>() };
    const modelSet = new Set<string>();
    const taskSet = new Set<string>();
    const g = new Map<string, FitnessEntry>();
    for (const entry of data.entries) {
      modelSet.add(entry.model);
      taskSet.add(entry.task_type);
      g.set(`${entry.model}::${entry.task_type}`, entry);
    }
    return {
      models: [...modelSet].sort(),
      taskTypes: [...taskSet].sort(),
      grid: g,
    };
  }, [data?.entries]);

  return (
    <CardShell title="Fitness Matrix" loading={isLoading} error={error ?? null} skeletonHeight="h-64">
      {models.length === 0 && (
        <p className="text-xs text-gray-500 py-4 text-center">No benchmark data yet</p>
      )}
      {models.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 border-b border-gray-800">
                <th className="text-left py-2 px-2 font-medium">Model</th>
                {taskTypes.map((t) => (
                  <th key={t} className="text-center py-2 px-2 font-medium">{t}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {models.map((model) => (
                <tr key={model} className="border-b border-gray-800/50">
                  <td className="py-2 px-2 font-mono text-gray-200 whitespace-nowrap">{model}</td>
                  {taskTypes.map((task) => {
                    const entry = grid.get(`${model}::${task}`);
                    if (!entry) return <td key={task} className="py-2 px-2 text-center text-gray-600">—</td>;
                    return (
                      <td key={task} className="py-2 px-2 text-center">
                        <QualityBadge quality={entry.avg_quality} />
                        <div className="text-[10px] text-gray-500 mt-0.5">
                          {formatUSD(entry.avg_cost)} · {formatMs(entry.avg_latency)}
                        </div>
                        <div className="text-[10px] text-gray-600">n={entry.sample_size}</div>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </CardShell>
  );
}

function QualityBadge({ quality }: { quality: number }) {
  const pct = (quality * 100).toFixed(0);
  const color = quality >= 0.8 ? "text-green-400" : quality >= 0.6 ? "text-yellow-400" : "text-red-400";
  return <span className={`font-mono font-medium ${color}`}>{pct}%</span>;
}

// -- Drift Detection ----------------------------------------------------------

function DriftDetection({ timeRange }: { timeRange: TimeRange }) {
  const { data, isLoading, error } = useBenchmarkDrift(timeRange);

  return (
    <CardShell title="Quality Drift" loading={isLoading} error={error ?? null} skeletonHeight="h-48">
      {data?.drifted.length === 0 && data?.stable.length === 0 && (
        <p className="text-xs text-gray-500 py-4 text-center">No drift data available</p>
      )}
      {data && data.drifted.length > 0 && (
        <div className="flex flex-col gap-1 mb-3">
          <span className="text-[10px] text-red-400 uppercase font-medium">Degraded</span>
          {data.drifted.map((d) => (
            <div key={`${d.model}-${d.task_type}`} className="flex items-center gap-2 px-2 py-1.5 bg-red-500/5 rounded">
              <span className="text-xs font-mono text-gray-200">{d.model}</span>
              <span className="text-[10px] text-gray-500">{d.task_type}</span>
              <span className="ml-auto text-xs font-mono text-red-400">{d.drift_pct.toFixed(1)}%</span>
            </div>
          ))}
        </div>
      )}
      {data && data.stable.length > 0 && (
        <div className="flex flex-col gap-1">
          <span className="text-[10px] text-green-400 uppercase font-medium">Stable</span>
          {data.stable.slice(0, 5).map((d) => (
            <div key={`${d.model}-${d.task_type}`} className="flex items-center gap-2 px-2 py-1.5 bg-gray-800/30 rounded">
              <span className="text-xs font-mono text-gray-200">{d.model}</span>
              <span className="text-[10px] text-gray-500">{d.task_type}</span>
              <span className="ml-auto text-xs font-mono text-green-400">{d.drift_pct.toFixed(1)}%</span>
            </div>
          ))}
        </div>
      )}
    </CardShell>
  );
}

// -- Recent Results -----------------------------------------------------------

function RecentResults({ timeRange }: { timeRange: TimeRange }) {
  const { data, isLoading, error } = useBenchmarkResults(timeRange, { limit: "20" });

  return (
    <CardShell title="Recent Results" loading={isLoading} error={error ?? null} skeletonHeight="h-48">
      {data?.results.length === 0 && (
        <p className="text-xs text-gray-500 py-4 text-center">No benchmark results yet</p>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-gray-500 border-b border-gray-800">
              <th className="text-left py-1.5 px-2 font-medium">Original</th>
              <th className="text-left py-1.5 px-2 font-medium">Benchmark</th>
              <th className="text-center py-1.5 px-2 font-medium">Quality</th>
              <th className="text-right py-1.5 px-2 font-medium">Cost Delta</th>
            </tr>
          </thead>
          <tbody>
            {data?.results.map((r) => {
              const costDelta = r.original_cost - r.benchmark_cost;
              return (
                <tr key={r.id} className="border-b border-gray-800/50">
                  <td className="py-1.5 px-2 font-mono text-gray-300 whitespace-nowrap">{r.original_model}</td>
                  <td className="py-1.5 px-2 font-mono text-gray-300 whitespace-nowrap">{r.benchmark_model}</td>
                  <td className="py-1.5 px-2 text-center"><QualityBadge quality={r.quality_score} /></td>
                  <td className={`py-1.5 px-2 text-right font-mono ${costDelta > 0 ? "text-green-400" : "text-red-400"}`}>
                    {costDelta > 0 ? "-" : "+"}{formatUSD(Math.abs(costDelta))}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </CardShell>
  );
}
