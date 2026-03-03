import { useEffect, type ReactNode } from "react";
import type { TimeRange } from "../../hooks/useStats";

interface ShellProps {
  timeRange: TimeRange;
  onTimeRangeChange: (range: TimeRange) => void;
  children: ReactNode;
}

const RANGES: { label: string; value: TimeRange; shortcut: string }[] = [
  { label: "24h", value: "24h", shortcut: "1" },
  { label: "7d", value: "7d", shortcut: "2" },
  { label: "30d", value: "30d", shortcut: "3" },
];

export function Shell({ timeRange, onTimeRangeChange, children }: ShellProps) {
  // Keyboard shortcuts: 1/2/3 switch time range
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      // Don't capture when typing in an input
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      const match = RANGES.find((r) => r.shortcut === e.key);
      if (match) onTimeRangeChange(match.value);
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onTimeRangeChange]);

  // Dynamic document title
  useEffect(() => {
    document.title = `AgentProof [${timeRange}]`;
  }, [timeRange]);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      <header className="border-b border-gray-800 bg-gray-900 px-4 sm:px-6 py-3 flex flex-wrap items-center justify-between gap-2 shrink-0">
        <div className="flex items-center gap-3">
          {/* Shield icon */}
          <svg
            width="16"
            height="16"
            viewBox="0 0 16 16"
            fill="none"
            className="shrink-0"
            aria-hidden="true"
          >
            <path
              d="M8 1L3 4v4.5c0 3.5 2.5 5.5 5 6.5 2.5-1 5-3 5-6.5V4L8 1z"
              fill="#7c3aed"
              stroke="#5b21b6"
              strokeWidth="0.5"
            />
            <path
              d="M8 5v4M6 7h4"
              stroke="white"
              strokeWidth="1.2"
              strokeLinecap="round"
            />
          </svg>
          <span className="text-sm font-semibold tracking-widest uppercase text-gray-100">
            AgentProof
          </span>
          <span className="text-xs text-gray-500 ml-1">dev dashboard</span>
        </div>

        <nav aria-label="Time range" className="flex gap-1">
          {RANGES.map(({ label, value, shortcut }) => (
            <button
              key={value}
              onClick={() => onTimeRangeChange(value)}
              aria-pressed={timeRange === value}
              title={`Switch to ${label} (${shortcut})`}
              className={[
                "px-3 py-1 text-xs font-mono rounded transition-colors",
                timeRange === value
                  ? "bg-violet-600/20 text-violet-300 ring-1 ring-violet-500/40"
                  : "text-gray-400 hover:text-gray-100 hover:bg-gray-800",
              ].join(" ")}
            >
              {label}
            </button>
          ))}
        </nav>
      </header>

      <main className="flex-1 px-4 sm:px-6 py-6 overflow-auto">
        {children}
      </main>
    </div>
  );
}
