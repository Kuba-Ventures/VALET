#!/usr/bin/env bash
# Smoke-test the Stage A proxy endpoints end to end.
#
# Usage:
#   PROXY_BASE=http://localhost:3000 LICENSE_KEY=PRODUCT-XXXX-... ./scripts/smoke-proxy.sh
#
# PROXY_BASE defaults to http://localhost:3000. LICENSE_KEY must be an active or
# trialing license (grab one from the Supabase `licenses` table). The completion
# and tts calls incur a few cents of real AI/TTS usage — that is the point.
set -uo pipefail

BASE="${PROXY_BASE:-http://localhost:3000}"
KEY="${LICENSE_KEY:?Set LICENSE_KEY to an active/trialing license key}"
H_LICENSE="X-License-Key: ${KEY}"
pass=0; fail=0
ok()  { echo "  PASS: $1"; pass=$((pass+1)); }
bad() { echo "  FAIL: $1"; fail=$((fail+1)); }

echo "== 1. usage status (GET /api/proxy/usage) =="
body="$(curl -sS -H "$H_LICENSE" "$BASE/api/proxy/usage")"
echo "  $body"
echo "$body" | grep -q '"allowance_usd"' && ok "usage returned allowance" || bad "no allowance in response"

echo "== 2. completion, non-stream (POST /api/proxy/completion) =="
body="$(curl -sS -H "$H_LICENSE" -H 'content-type: application/json' \
  -d '{"model":"claude-haiku-4-5","max_tokens":32,"messages":[{"role":"user","content":"Say only: online."}]}' \
  "$BASE/api/proxy/completion")"
echo "  $body"
echo "$body" | grep -q '"usage"' && ok "completion returned a message" || bad "completion failed"

echo "== 3. completion, streaming (SSE) =="
code="$(curl -sS -o /tmp/proxy_stream.txt -w '%{http_code}' -H "$H_LICENSE" -H 'content-type: application/json' \
  -d '{"model":"claude-haiku-4-5","max_tokens":32,"stream":true,"messages":[{"role":"user","content":"Count to three."}]}' \
  "$BASE/api/proxy/completion")"
grep -q 'message_start' /tmp/proxy_stream.txt && [ "$code" = "200" ] && ok "stream produced SSE events" || bad "stream failed (http $code)"

echo "== 4. tts (POST /api/proxy/tts) =="
code="$(curl -sS -o /tmp/proxy_tts.bin -w '%{http_code}' -H "$H_LICENSE" -H 'content-type: application/json' \
  -d '{"text":"Systems online.","format":"mp3"}' "$BASE/api/proxy/tts")"
size="$(wc -c < /tmp/proxy_tts.bin | tr -d ' ')"
[ "$code" = "200" ] && [ "$size" -gt 1000 ] && ok "tts returned ${size} bytes of audio" || bad "tts failed (http $code, ${size} bytes)"

echo "== 5. bad license is rejected (expect 402) =="
code="$(curl -sS -o /dev/null -w '%{http_code}' -H "X-License-Key: PRODUCT-NOPE-NOPE-NOPE-NOPE" \
  "$BASE/api/proxy/usage")"
[ "$code" = "402" ] || [ "$code" = "401" ] && ok "rejected invalid license (http $code)" || bad "did not reject invalid license (http $code)"

echo
echo "== usage after the run (should show nonzero cost) =="
curl -sS -H "$H_LICENSE" "$BASE/api/proxy/usage"
echo
echo
echo "RESULT: ${pass} passed, ${fail} failed"
[ "$fail" -eq 0 ]
