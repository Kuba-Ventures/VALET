import { NextRequest } from "next/server";
import { handleAnthropicProxy } from "@/lib/proxy/anthropic";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
// Opus + web tools can run for minutes. 300s is the safe ceiling on most Vercel
// plans; with Fluid Compute on Pro this can be raised. If long research streams
// start truncating, this single endpoint is the one to peel onto a worker — the
// app's proxy-base-URL config makes that a one-line change.
export const maxDuration = 300;

/**
 * POST /api/proxy/research
 * Header:  X-License-Key: PRODUCT-....
 * Body:    Anthropic Messages API request (Opus deep research, web tools, streaming).
 */
export async function POST(req: NextRequest) {
  return handleAnthropicProxy(req, "research");
}
