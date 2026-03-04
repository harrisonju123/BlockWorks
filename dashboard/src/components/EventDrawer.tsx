import { useEffect, useCallback, useRef } from "react";
import { useEvent } from "../hooks/useEvents";
import { formatUSD, formatMs } from "../utils/format";
import type { LLMEvent } from "../api/types";

interface Props {
  eventId: string | null;
  onClose: () => void;
}

/**
 * Slide-over drawer showing full event details.
 * Fetches the event by ID when opened and renders all fields
 * from LLMEvent plus routing info when present.
 */
export function EventDrawer({ eventId, onClose }: Props) {
  const { data: event, isLoading, error } = useEvent(eventId);
  const overlayRef = useRef<HTMLDivElement>(null);

  // Close on Escape key + lock body scroll
  useEffect(() => {
    if (!eventId) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", handleKey);
    return () => {
      document.body.style.overflow = "";
      window.removeEventListener("keydown", handleKey);
    };
  }, [eventId, onClose]);

  // Close on click outside the drawer panel
  const handleOverlayClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === overlayRef.current) onClose();
    },
    [onClose],
  );

  if (!eventId) return null;

  return (
    <div
      ref={overlayRef}
      onClick={handleOverlayClick}
      role="dialog"
      aria-modal="true"
      aria-label="Event detail"
      className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm transition-opacity"
    >
      <div className="absolute right-0 top-0 bottom-0 w-full max-w-lg bg-gray-900 border-l border-gray-800 shadow-2xl flex flex-col animate-slide-in-right">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-800 shrink-0">
          <h2 className="text-sm font-semibold text-gray-100">Event Detail</h2>
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded hover:bg-gray-800 transition-colors text-gray-400 hover:text-gray-100"
            aria-label="Close drawer"
          >
            <svg className="w-4 h-4" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
              <path d="M4 4l8 8M12 4l-8 8" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">
          {isLoading && (
            <div className="flex flex-col gap-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-5 bg-gray-800 rounded animate-pulse" />
              ))}
            </div>
          )}

          {error && (
            <div className="text-xs text-red-400 py-4 text-center">
              {(error as Error).message}
            </div>
          )}

          {event && !isLoading && <EventBody event={event} />}
        </div>
      </div>
    </div>
  );
}

function EventBody({ event }: { event: LLMEvent }) {
  return (
    <div className="flex flex-col gap-5">
      {/* Status + Model header */}
      <div className="flex items-center gap-3">
        <span
          className={`inline-block px-2 py-0.5 rounded text-[11px] font-medium ${
            event.status === "success"
              ? "bg-green-500/15 text-green-400"
              : "bg-red-500/15 text-red-400"
          }`}
        >
          {event.status}
        </span>
        <span className="font-mono text-sm text-gray-100">{event.model}</span>
      </div>

      {/* Core fields */}
      <Section title="General">
        <Row label="ID" value={event.id} mono />
        <Row label="Provider" value={event.provider} />
        <Row label="Model" value={event.model} mono />
        <Row
          label="Created"
          value={new Date(event.created_at).toLocaleString("en-US", {
            year: "numeric",
            month: "short",
            day: "numeric",
            hour: "numeric",
            minute: "2-digit",
            second: "2-digit",
          })}
        />
        <Row label="Status" value={event.status} />
      </Section>

      {/* Tokens */}
      <Section title="Tokens">
        <Row label="Prompt" value={event.prompt_tokens.toLocaleString()} mono />
        <Row label="Completion" value={event.completion_tokens.toLocaleString()} mono />
        <Row label="Total" value={event.total_tokens.toLocaleString()} mono />
      </Section>

      {/* Cost & Latency */}
      <Section title="Cost & Latency">
        <Row label="Estimated Cost" value={formatUSD(event.estimated_cost)} mono />
        <Row label="Latency" value={formatMs(event.latency_ms)} mono />
      </Section>

      {/* Trace Context */}
      <Section title="Trace Context">
        <Row label="Trace ID" value={event.trace_id} mono />
        <Row label="Span ID" value={event.span_id} mono />
      </Section>

      {/* Classification */}
      <Section title="Classification">
        <Row label="Task Type" value={event.task_type ?? "---"} />
        <Row
          label="Confidence"
          value={
            event.task_type_confidence != null
              ? `${(event.task_type_confidence * 100).toFixed(1)}%`
              : "---"
          }
          mono
        />
      </Section>

      {/* Agent & Tool Info */}
      <Section title="Agent & Tools">
        <Row label="Agent Framework" value={event.agent_framework ?? "---"} />
        <Row label="Has Tool Calls" value={event.has_tool_calls ? "Yes" : "No"} />
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <h3 className="text-[10px] font-medium uppercase tracking-wider text-gray-500 mb-0.5">
        {title}
      </h3>
      {children}
    </div>
  );
}

function Row({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4">
      <span className="text-xs text-gray-400 shrink-0">{label}</span>
      <span
        className={`text-xs text-gray-200 text-right break-all ${mono ? "font-mono" : ""}`}
      >
        {value}
      </span>
    </div>
  );
}
