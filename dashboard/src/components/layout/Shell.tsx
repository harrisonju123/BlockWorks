import { useEffect, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
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

const NAV_ITEMS = [
  { to: "/", label: "Overview", icon: ChartIcon },
  { to: "/events", label: "Events", icon: ListIcon },
  { to: "/alerts", label: "Alerts", icon: BellIcon },
  { to: "/benchmarks", label: "Benchmarks", icon: BeakerIcon },
  { to: "/waste", label: "Waste", icon: ScaleIcon },
  { to: "/routing", label: "Routing", icon: RouteIcon },
  { to: "/mcp", label: "MCP Tracing", icon: PlugIcon },
  { to: "/attestations", label: "Attestations", icon: ShieldIcon },
] as const;

export function Shell({ timeRange, onTimeRangeChange, children }: ShellProps) {
  const location = useLocation();

  // Keyboard shortcuts: 1/2/3 switch time range
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      const match = RANGES.find((r) => r.shortcut === e.key);
      if (match) onTimeRangeChange(match.value);
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [onTimeRangeChange]);

  // Dynamic document title based on current page
  useEffect(() => {
    const current = NAV_ITEMS.find((item) =>
      item.to === "/" ? location.pathname === "/" : location.pathname.startsWith(item.to)
    );
    const page = current?.label ?? "AgentProof";
    document.title = `${page} [${timeRange}] — AgentProof`;
  }, [timeRange, location.pathname]);

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex">
      {/* Sidebar */}
      <aside className="w-52 shrink-0 bg-gray-900 border-r border-gray-800 flex flex-col">
        {/* Logo */}
        <div className="px-4 py-4 flex items-center gap-2 border-b border-gray-800">
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
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-2 py-3 flex flex-col gap-0.5">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                [
                  "flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition-colors",
                  isActive
                    ? "bg-violet-600/15 text-violet-300"
                    : "text-gray-400 hover:text-gray-100 hover:bg-gray-800",
                ].join(" ")
              }
            >
              <Icon className="w-4 h-4 shrink-0" />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* Footer */}
        <div className="px-4 py-3 border-t border-gray-800">
          <span className="text-[10px] text-gray-600 uppercase tracking-wider">dev</span>
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar with time range */}
        <header className="border-b border-gray-800 bg-gray-900/50 px-4 sm:px-6 py-3 flex items-center justify-end shrink-0">
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
    </div>
  );
}

// -- Inline SVG icons (small enough to avoid a dependency) -------------------

function ChartIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M2 14V6l4 4 3-6 5 4" />
    </svg>
  );
}

function ListIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round">
      <path d="M5 4h9M5 8h9M5 12h7M2 4h.01M2 8h.01M2 12h.01" />
    </svg>
  );
}

function BellIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 13a2 2 0 004 0M4 7a4 4 0 018 0c0 2 1 4 2 5H2c1-1 2-3 2-5z" />
    </svg>
  );
}

function BeakerIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 2v4L2 14h12L10 6V2M5 2h6M6 8h4" />
    </svg>
  );
}

function ScaleIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 2v12M3 5l5-3 5 3M1 9l2-4 2 4a3 3 0 01-4 0zM11 9l2-4 2 4a3 3 0 01-4 0z" />
    </svg>
  );
}

function RouteIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="3" cy="13" r="1.5" />
      <circle cx="13" cy="3" r="1.5" />
      <path d="M4.5 13C7 13 9 10 9 8s2-5 4.5-5" />
    </svg>
  );
}

function PlugIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 2v4M10 2v4M4 6h8v2a4 4 0 01-4 4 4 4 0 01-4-4V6zM8 12v2" />
    </svg>
  );
}

function ShieldIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 1L3 4v4.5c0 3.5 2.5 5.5 5 6.5 2.5-1 5-3 5-6.5V4L8 1z" />
      <path d="M6 8l2 2 3-4" />
    </svg>
  );
}
