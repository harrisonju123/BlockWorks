import type {
  StatsSummary,
  TimeseriesResponse,
  TopTrace,
  WasteScore,
  EventsResponse,
} from "./types";

const API_BASE = "/api/v1";

async function fetchJson<T>(url: string, params?: Record<string, string>): Promise<T> {
  const searchParams = new URLSearchParams(params);
  const fullUrl = params ? `${url}?${searchParams}` : url;
  const res = await fetch(fullUrl);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function getSummary(
  start?: string,
  end?: string,
  groupBy = "model"
): Promise<StatsSummary> {
  const params: Record<string, string> = { group_by: groupBy };
  if (start) params.start = start;
  if (end) params.end = end;
  return fetchJson(`${API_BASE}/stats/summary`, params);
}

export async function getTimeseries(
  metric = "cost",
  interval = "1h",
  start?: string,
  end?: string
): Promise<TimeseriesResponse> {
  const params: Record<string, string> = { metric, interval };
  if (start) params.start = start;
  if (end) params.end = end;
  return fetchJson(`${API_BASE}/stats/timeseries`, params);
}

export async function getTopTraces(
  sortBy = "cost",
  limit = 10,
  start?: string,
  end?: string
): Promise<{ traces: TopTrace[] }> {
  const params: Record<string, string> = { sort_by: sortBy, limit: String(limit) };
  if (start) params.start = start;
  if (end) params.end = end;
  return fetchJson(`${API_BASE}/stats/top-traces`, params);
}

export async function getWasteScore(
  start?: string,
  end?: string
): Promise<WasteScore> {
  const params: Record<string, string> = {};
  if (start) params.start = start;
  if (end) params.end = end;
  return fetchJson(`${API_BASE}/stats/waste-score`, params);
}

export async function getEvents(
  params: Record<string, string> = {}
): Promise<EventsResponse> {
  return fetchJson(`${API_BASE}/events`, params);
}
