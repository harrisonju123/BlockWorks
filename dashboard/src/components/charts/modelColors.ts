/**
 * Stable model color assignments so the same model always renders with the
 * same color across every chart on the page. We key on lowercased model name
 * substring matching so "claude-3-5-sonnet" and "claude-3-opus" both resolve
 * to violet without enumerating every variant.
 */
const MODEL_PALETTE: Array<[RegExp, string]> = [
  [/claude/i, "#7c3aed"],   // violet
  [/gpt|openai/i, "#16a34a"], // green
  [/gemini|google/i, "#2563eb"], // blue
  [/mistral/i, "#d97706"],  // amber
  [/llama|meta/i, "#dc2626"], // red
  [/cohere/i, "#0891b2"],   // cyan
];

const FALLBACK_COLORS = [
  "#6b7280", "#9333ea", "#0ea5e9", "#f59e0b", "#10b981", "#f43f5e",
];

// Cache so repeated calls for the same key are O(1).
const cache = new Map<string, string>();
let fallbackIndex = 0;

export function modelColor(modelName: string): string {
  const key = modelName.toLowerCase();
  if (cache.has(key)) return cache.get(key)!;

  for (const [pattern, color] of MODEL_PALETTE) {
    if (pattern.test(key)) {
      cache.set(key, color);
      return color;
    }
  }

  // Unknown model — pull from the fallback list round-robin so we at least
  // get distinct colors for whatever new models show up.
  const color = FALLBACK_COLORS[fallbackIndex % FALLBACK_COLORS.length];
  fallbackIndex++;
  cache.set(key, color);
  return color;
}
