#!/usr/bin/env bash
set -euo pipefail

BASE="${PROOFRAIL_API_BASE:-http://127.0.0.1:8000}"
ADMIN_KEY="${PROOFRAIL_ADMIN_KEY:?Set PROOFRAIL_ADMIN_KEY to match the API}"

echo "Using API base: ${BASE}"

echo "== Create API key (admin) =="
KEY_JSON="$(curl -sS -X POST "${BASE}/v1/admin/keys" \
  -H "x-admin-key: ${ADMIN_KEY}" \
  -H "content-type: application/json" \
  -d '{"customer_id":"demo-curl","scopes":["write:screen","read:evidence","write:cases"]}')"
API_KEY="$(printf '%s' "${KEY_JSON}" | python3 -c 'import sys, json; print(json.load(sys.stdin)["api_key"])')"
echo "Issued key for customer demo-curl (not printing full secret)."

echo "== POST /v2/screenings =="
SCREEN_JSON="$(curl -sS -X POST "${BASE}/v2/screenings" \
  -H "x-api-key: ${API_KEY}" \
  -H "content-type: application/json" \
  -d '{"screening_type":"onboarding","subject":{"name":"Jane Demo","country":"US"}}')"
EVIDENCE_PACK_ID="$(printf '%s' "${SCREEN_JSON}" | python3 -c 'import sys, json; print(json.load(sys.stdin)["evidence_pack_id"])')"
CASE_ID="$(printf '%s' "${SCREEN_JSON}" | python3 -c 'import sys, json; print(json.load(sys.stdin)["screening_id"])')"
echo "screening_id / case_id: ${CASE_ID}"
echo "evidence_pack_id: ${EVIDENCE_PACK_ID}"

echo "== GET /v2/evidence-packs/{id}/export?format=json (first 400 bytes) =="
curl -sS "${BASE}/v2/evidence-packs/${EVIDENCE_PACK_ID}/export?format=json" \
  -H "x-api-key: ${API_KEY}" \
  | head -c 400
echo
echo "… (truncated)"

echo "Done."
