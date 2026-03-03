import type { ReactNode } from "react";
import type { TimeRange } from "../../hooks/useStats";

interface ShellProps {
  timeRange: TimeRange;
  onTimeRangeChange: (range: TimeRange) => void;
  children: ReactNode;
}

const RANGES: { label: string; value: TimeRange }[] = [
  { label: "24h", value: "24h" },
  { label: "7d", value: "7d" },
  { label: "30d", value: "30d" },
];

export function Shell({ timeRange, onTimeRangeChange, children }: ShellProps) {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      <header className="border-b border-gray-800 bg-gray-900 px-6 py-3 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-3">
          {/* Small accent dot to make the brand mark a bit less bare */}
          <span className="block w-2 h-2 rounded-full bg-violet-500" aria-hidden="true" />
          <span className="text-sm font-semibold tracking-widest uppercase text-gray-100">
            AgentProof
          </span>
          <span className="text-xs text-gray-500 ml-1">dev dashboard</span>
        </div>

        <nav aria-label="Time range" className="flex gap-1">
          {RANGES.map(({ label, value }) => (
            <button
              key={value}
              onClick={() => onTimeRangeChange(value)}
              aria-pressed={timeRange === value}
              className={[
                "px-3 py-1 text-xs font-mono rounded transition-colors",
                timeRange === value
                  ? "bg-violet-700 text-white"
                  : "text-gray-400 hover:text-gray-100 hover:bg-gray-800",
              ].join(" ")}
            >
              {label}
            </button>
          ))}
        </nav>
      </header>

      <main className="flex-1 px-6 py-6 overflow-auto">
        {children}
      </main>
    </div>
  );
}
