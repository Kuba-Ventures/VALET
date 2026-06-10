import { NextRequest, NextResponse } from "next/server";
import { getSupabaseAdmin, type LicenseStatus } from "@/lib/supabase";
import { isEntitled } from "@/lib/license";

/**
 * License authorization for every proxy endpoint. The desktop app sends its
 * license key in the `X-License-Key` header (no vendor secrets ever leave the
 * proxy). We resolve the key against the Phase-1 licenses table and only let
 * active/trialing licenses through.
 */

export interface Authorized {
  ok: true;
  licenseKey: string;
  status: LicenseStatus;
}

export interface Unauthorized {
  ok: false;
  response: NextResponse;
}

const LICENSE_HEADER = "x-license-key";

export async function authorizeLicense(
  req: NextRequest,
): Promise<Authorized | Unauthorized> {
  const licenseKey = (req.headers.get(LICENSE_HEADER) ?? "").trim();

  if (!licenseKey) {
    return {
      ok: false,
      response: NextResponse.json(
        { error: "Missing license key.", status: "invalid" },
        { status: 401 },
      ),
    };
  }

  let status: LicenseStatus = "invalid";
  try {
    const supabase = getSupabaseAdmin();
    const { data, error } = await supabase
      .from("licenses")
      .select("status")
      .eq("license_key", licenseKey)
      .maybeSingle();
    if (error) {
      return {
        ok: false,
        response: NextResponse.json(
          { error: "License lookup failed." },
          { status: 500 },
        ),
      };
    }
    status = (data?.status as LicenseStatus) ?? "invalid";
  } catch (err) {
    const message = err instanceof Error ? err.message : "Store unavailable.";
    return { ok: false, response: NextResponse.json({ error: message }, { status: 500 }) };
  }

  if (!isEntitled(status)) {
    // 402 Payment Required: the app distinguishes this from auth/transport
    // errors and prompts the user to renew or check billing.
    return {
      ok: false,
      response: NextResponse.json(
        { error: "An active or trialing license is required.", status },
        { status: 402 },
      ),
    };
  }

  return { ok: true, licenseKey, status };
}
