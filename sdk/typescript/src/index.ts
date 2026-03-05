/**
 * BlockThrough TypeScript SDK
 *
 * Provides a typed client for the BlockThrough REST API. Uses the
 * native fetch API (Node 18+ / browsers) with no external dependencies.
 *
 * Usage:
 *   import { BlockThroughClient } from "@blockthrough/sdk";
 *
 *   const client = new BlockThroughClient({ apiUrl: "http://localhost:8100" });
 *   await client.track({ model: "gpt-4o", messages: [...], ... });
 *   const stats = await client.getStats();
 */

import type {
  BlockThroughConfig,
  FitnessMatrixResponse,
  SummaryResponse,
  TrackEventRequest,
  TrackEventResponse,
  WasteScoreResponse,
} from "./types.js";

export * from "./types.js";

export class BlockThroughError extends Error {
  constructor(
    public readonly statusCode: number,
    public readonly detail: string,
  ) {
    super(`BlockThrough API error ${statusCode}: ${detail}`);
    this.name = "BlockThroughError";
  }
}

export class BlockThroughClient {
  private readonly baseUrl: string;
  private readonly headers: Record<string, string>;
  private readonly timeoutMs: number;
  private readonly maxRetries: number;

  constructor(config: BlockThroughConfig) {
    this.baseUrl = config.apiUrl.replace(/\/$/, "");
    this.timeoutMs = config.timeoutMs ?? 30_000;
    this.maxRetries = config.maxRetries ?? 3;

    this.headers = {
      "Content-Type": "application/json",
    };
    if (config.apiKey) {
      this.headers["Authorization"] = `Bearer ${config.apiKey}`;
    }
  }

  private async request<T>(
    method: string,
    path: string,
    options?: {
      body?: unknown;
      params?: Record<string, string | undefined>;
    },
  ): Promise<T> {
    let url = `${this.baseUrl}${path}`;

    // Append query parameters
    if (options?.params) {
      const searchParams = new URLSearchParams();
      for (const [key, value] of Object.entries(options.params)) {
        if (value !== undefined) {
          searchParams.set(key, value);
        }
      }
      const qs = searchParams.toString();
      if (qs) {
        url += `?${qs}`;
      }
    }

    let lastError: Error | null = null;

    for (let attempt = 0; attempt <= this.maxRetries; attempt++) {
      try {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), this.timeoutMs);

        const response = await fetch(url, {
          method,
          headers: this.headers,
          body: options?.body ? JSON.stringify(options.body) : undefined,
          signal: controller.signal,
        });

        clearTimeout(timeoutId);

        if (!response.ok) {
          const text = await response.text();
          let detail = text;
          try {
            const json = JSON.parse(text);
            detail = json.detail ?? text;
          } catch {
            // Use raw text as detail
          }
          throw new BlockThroughError(response.status, detail);
        }

        return (await response.json()) as T;
      } catch (error) {
        lastError = error as Error;

        // Don't retry client errors (4xx) or if we've exhausted retries
        if (
          error instanceof BlockThroughError &&
          error.statusCode >= 400 &&
          error.statusCode < 500
        ) {
          throw error;
        }

        if (attempt === this.maxRetries) {
          throw error;
        }

        // Exponential backoff: 100ms, 200ms, 400ms, ...
        await new Promise((resolve) =>
          setTimeout(resolve, 100 * Math.pow(2, attempt)),
        );
      }
    }

    throw lastError ?? new Error("Request failed");
  }

  /**
   * Report a single LLM call event.
   */
  async track(event: TrackEventRequest): Promise<TrackEventResponse> {
    const payload = {
      id: crypto.randomUUID(),
      created_at: new Date().toISOString(),
      status: event.status ?? "success",
      provider: event.provider ?? "custom",
      model: event.model,
      prompt_tokens: event.prompt_tokens,
      completion_tokens: event.completion_tokens,
      total_tokens: event.prompt_tokens + event.completion_tokens,
      estimated_cost: event.estimated_cost,
      latency_ms: event.latency_ms,
      prompt_hash: "", // Hashing is done server-side for TS SDK
      completion_hash: "",
      trace_id: event.trace_id ?? crypto.randomUUID(),
      span_id: crypto.randomUUID(),
      litellm_call_id: `sdk-ts-${crypto.randomUUID()}`,
      session_id: event.session_id,
      org_id: event.org_id,
      user_id: event.user_id,
      custom_metadata: event.metadata,
    };

    return this.request<TrackEventResponse>("POST", "/api/v1/events/ingest", {
      body: payload,
    });
  }

  /**
   * Fetch summary stats for the current period.
   */
  async getStats(options?: {
    groupBy?: string;
    orgId?: string;
  }): Promise<SummaryResponse> {
    return this.request<SummaryResponse>("GET", "/api/v1/stats/summary", {
      params: {
        group_by: options?.groupBy ?? "model",
        org_id: options?.orgId,
      },
    });
  }

  /**
   * Fetch the current waste score.
   */
  async getWasteScore(options?: {
    orgId?: string;
  }): Promise<WasteScoreResponse> {
    return this.request<WasteScoreResponse>(
      "GET",
      "/api/v1/stats/waste-score",
      {
        params: { org_id: options?.orgId },
      },
    );
  }

  /**
   * Fetch the fitness matrix from benchmark results.
   */
  async getFitnessMatrix(options?: {
    orgId?: string;
  }): Promise<FitnessMatrixResponse> {
    return this.request<FitnessMatrixResponse>(
      "GET",
      "/api/v1/benchmarks/fitness-matrix",
      {
        params: { org_id: options?.orgId },
      },
    );
  }
}
