import { NextRequest } from "next/server";
import { handleTtsProxy } from "@/lib/proxy/fish";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 60;

/**
 * POST /api/proxy/tts
 * Header:  X-License-Key: PRODUCT-....
 * Body:    { text: string, format?: "mp3" | "wav" | "pcm", reference_id?: string }
 * Returns: audio bytes (streamed).
 */
export async function POST(req: NextRequest) {
  return handleTtsProxy(req);
}
