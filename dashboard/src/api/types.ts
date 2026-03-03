export interface StatsSummary {
  period: { start: string; end: string };
  total_requests: number;
  total_cost_usd: number;
  total_tokens: number;
  failure_rate: number;
  groups: StatGroup[];
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

export interface TimeseriesResponse {
  metric: "cost" | "requests" | "latency" | "tokens";
  interval: "1h" | "6h" | "1d";
  data: TimeseriesPoint[];
}

export interface TimeseriesPoint {
  timestamp: string;
  value: number;
}

export interface TopTrace {
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

export interface WasteScore {
  waste_score: number;
  total_potential_savings_usd: number;
  breakdown: WasteBreakdownItem[];
}

export interface WasteBreakdownItem {
  task_type: string;
  current_model: string;
  suggested_model: string;
  call_count: number;
  current_cost_usd: number;
  projected_cost_usd: number;
  savings_usd: number;
  confidence: number;
}

export interface EventsResponse {
  events: LLMEvent[];
  total_count: number;
  has_more: boolean;
}

export interface LLMEvent {
  id: string;
  created_at: string;
  status: "success" | "failure";
  provider: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost: number;
  latency_ms: number;
  trace_id: string;
  span_id: string;
  task_type: string | null;
  task_type_confidence: number | null;
  has_tool_calls: boolean;
  agent_framework: string | null;
}
