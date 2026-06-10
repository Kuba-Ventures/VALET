/**
 * Model pricing and cost estimation for proxy metering.
 *
 * Rates are USD per 1M tokens (input, output), matched by model-ID prefix so a
 * dated alias like "claude-haiku-4-5-20251001" resolves to the "claude-haiku-4-5"
 * row. Keep this table in sync with platform.claude.com/docs/en/pricing.
 */

export interface TokenUsage {
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
}

interface Rate {
  input: number; // USD per 1M input tokens
  output: number; // USD per 1M output tokens
}

// Longest prefixes first so "claude-opus-4-8" wins over a hypothetical "claude-opus".
const MODEL_RATES: Array<[string, Rate]> = [
  ["claude-fable-5", { input: 10.0, output: 50.0 }],
  ["claude-opus-4-8", { input: 5.0, output: 25.0 }],
  ["claude-opus-4-7", { input: 5.0, output: 25.0 }],
  ["claude-opus-4-6", { input: 5.0, output: 25.0 }],
  ["claude-opus-4-5", { input: 5.0, output: 25.0 }],
  ["claude-sonnet-4-6", { input: 3.0, output: 15.0 }],
  ["claude-haiku-4-5", { input: 1.0, output: 5.0 }],
];

// Unknown model: fall back to Opus-tier rates so we never under-bill a new model.
const FALLBACK_RATE: Rate = { input: 5.0, output: 25.0 };

function rateFor(model: string): Rate {
  for (const [prefix, rate] of MODEL_RATES) {
    if (model.startsWith(prefix)) return rate;
  }
  return FALLBACK_RATE;
}

/**
 * Estimated USD cost of one model call. Cache writes bill ~1.25x input, cache
 * reads ~0.1x input — mirror that so heavy-cache callers are metered fairly.
 */
export function estimateModelCost(model: string, usage: TokenUsage): number {
  const rate = rateFor(model);
  const inputCost =
    (usage.input_tokens / 1_000_000) * rate.input +
    (usage.cache_creation_input_tokens / 1_000_000) * rate.input * 1.25 +
    (usage.cache_read_input_tokens / 1_000_000) * rate.input * 0.1;
  const outputCost = (usage.output_tokens / 1_000_000) * rate.output;
  return inputCost + outputCost;
}

/**
 * Estimated USD cost of one TTS call, metered by character count. Fish Audio
 * does not return token usage, so we price on input length at a configurable
 * rate (FISH_USD_PER_MILLION_CHARS, default $15/1M chars).
 */
export function estimateTtsCost(chars: number): number {
  const perMillion = Number(process.env.FISH_USD_PER_MILLION_CHARS ?? "15");
  return (chars / 1_000_000) * (Number.isFinite(perMillion) ? perMillion : 15);
}
