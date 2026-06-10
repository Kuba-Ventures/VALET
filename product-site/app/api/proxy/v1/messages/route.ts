import { NextRequest } from "next/server";
import { handleAnthropicProxy } from "@/lib/proxy/anthropic";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
// Serves both conversation and research, so use the longer research ceiling.
export const maxDuration = 300;

/**
 * POST /api/proxy/v1/messages — Anthropic-native passthrough.
 *
 * This is the endpoint the desktop app's Anthropic SDK hits: it sets
 * base_url = "<proxy>/api/proxy", so the SDK appends "/v1/messages" here. The
 * license travels in the X-License-Key header; the action ("completion" vs
 * "research", for metering/Langfuse tags) travels in an optional X-Action-Type
 * header and defaults to "completion".
 *
 * The dedicated /api/proxy/{completion,research} routes remain for direct
 * callers; this one exists so the SDK works with only a base_url + header swap.
 */
export async function POST(req: NextRequest) {
  const action = (req.headers.get("x-action-type") || "completion").toLowerCase();
  return handleAnthropicProxy(req, action === "research" ? "research" : "completion");
}
