/**
 * BlockThrough TypeScript SDK Types
 *
 * Mirrors the Python API schemas so TypeScript consumers get
 * full type safety. Each interface maps 1:1 to a Pydantic model
 * in src/blockthrough/api/schemas.py.
 */

// -- Enums -------------------------------------------------------------------

export type EventStatus = "success" | "failure";

export type TaskType =
  | "code_generation"
  | "classification"
  | "summarization"
  | "extraction"
  | "reasoning"
  | "conversation"
  | "tool_selection"
  | "unknown";

// -- Event tracking ----------------------------------------------------------

export interface TrackEventRequest {
  model: string;
  messages: Array<{ role: string; content: string }>;
  completion: string;
  prompt_tokens: number;
  completion_tokens: number;
  estimated_cost: number;
  latency_ms: number;
  provider?: string;
  status?: EventStatus;
  session_id?: string;
  trace_id?: string;
  org_id?: string;
  user_id?: string;
  metadata?: Record<string, unknown>;
}

export interface TrackEventResponse {
  event_id: string;
  created_at: string;
  status: string;
}

// -- Summary stats -----------------------------------------------------------

export interface Period {
  start: string;
  end: string;
}

export interface StatGroup {
  key: string;
  request_count: number;
  total_cost_usd: number;
  avg_latency_ms: number;
  p95_latency_ms: number;
  avg_cost_per_request_usd: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  failure_count: number;
}

export interface SummaryResponse {
  period: Period;
  total_requests: number;
  total_cost_usd: number;
  total_tokens: number;
  failure_rate: number;
  groups: StatGroup[];
}

// -- Timeseries --------------------------------------------------------------

export interface TimeseriesPoint {
  timestamp: string;
  value: number;
}

export interface TimeseriesResponse {
  metric: string;
  interval: string;
  data: TimeseriesPoint[];
}

// -- Traces ------------------------------------------------------------------

export interface TraceInfo {
  trace_id: string;
  total_cost_usd: number;
  total_tokens: number;
  total_latency_ms: number;
  event_count: number;
  models_used: string[];
  first_event_at: string;
  last_event_at: string;
  agent_framework: string | null;
}

export interface TopTracesResponse {
  traces: TraceInfo[];
}

// -- Waste score -------------------------------------------------------------

export interface WasteBreakdownItem {
  task_type: TaskType;
  current_model: string;
  suggested_model: string;
  call_count: number;
  current_cost_usd: number;
  projected_cost_usd: number;
  savings_usd: number;
  confidence: number;
}

export interface WasteScoreResponse {
  waste_score: number;
  total_potential_savings_usd: number;
  breakdown: WasteBreakdownItem[];
}

// -- Waste details -----------------------------------------------------------

export interface WasteDetailItem {
  category: string;
  severity: string;
  affected_trace_ids: string[];
  call_count: number;
  current_cost: number;
  projected_cost: number;
  savings: number;
  description: string;
  confidence: number;
}

export interface WasteReportResponse {
  items: WasteDetailItem[];
  total_savings: number;
  total_spend: number;
  waste_score: number;
  generated_at: string | null;
}

// -- Events ------------------------------------------------------------------

export interface EventDetail {
  id: string;
  created_at: string;
  status: EventStatus;
  provider: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost: number;
  latency_ms: number;
  trace_id: string;
  span_id: string;
  task_type: TaskType | null;
  task_type_confidence: number | null;
  has_tool_calls: boolean;
  agent_framework: string | null;
}

export interface EventsResponse {
  events: EventDetail[];
  total_count: number;
  has_more: boolean;
}

// -- Fitness matrix ----------------------------------------------------------

export interface FitnessEntry {
  model: string;
  task_type: string;
  quality_score: number;
  avg_cost: number;
  avg_latency_ms: number;
  sample_count: number;
}

export interface FitnessMatrixResponse {
  entries: FitnessEntry[];
}

// -- Health ------------------------------------------------------------------

export interface HealthResponse {
  status: string;
  db: string;
  version: string;
}

// -- SDK Config --------------------------------------------------------------

export interface BlockThroughConfig {
  apiUrl: string;
  apiKey?: string;
  timeoutMs?: number;
  maxRetries?: number;
}
