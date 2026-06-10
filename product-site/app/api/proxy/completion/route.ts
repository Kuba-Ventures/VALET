import { NextRequest } from "next/server";
import { handleAnthropicProxy } from "@/lib/proxy/anthropic";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 60;

/**
 * POST /api/proxy/completion
 * Header:  X-License-Key: PRODUCT-....
 * Body:    Anthropic Messages API request (fast Haiku conversation; may stream).
 */
export async function POST(req: NextRequest) {
  return handleAnthropicProxy(req, "completion");
}
