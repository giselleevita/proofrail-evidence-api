#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT_DIR}/output"
mkdir -p "${OUT_DIR}"

BASE_URL="${PROOFRAIL_BASE_URL:-http://127.0.0.1:8000}"
ADMIN_KEY="${PROOFRAIL_ADMIN_KEY:-change-me}"
CUSTOMER_ID="${PROOFRAIL_CUSTOMER_ID:-demo-$(date +%Y%m%d-%H%M%S)}"

echo "==> Waiting for API readiness at ${BASE_URL}/readyz"
for i in $(seq 1 60); do
  if curl -sf "${BASE_URL}/readyz" >/dev/null; then
    echo "ready"
    break
  fi
  sleep 0.5
  if [[ "${i}" == "60" ]]; then
    echo "ERROR: API not ready after 30s" >&2
    exit 1
  fi
done

echo "==> Creating API key (customer: ${CUSTOMER_ID})"
API_KEY="$(
  curl -sS -X POST "${BASE_URL}/v1/admin/keys" \
    -H "content-type: application/json" \
    -H "x-admin-key: ${ADMIN_KEY}" \
    -d "{\"customer_id\":\"${CUSTOMER_ID}\",\"scopes\":[\"write:screen\",\"read:evidence\",\"write:cases\"]}" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["api_key"])'
)"

echo "==> Screening (v2) no-hit: Alice Example"
NO_HIT="$(
  curl -sS -X POST "${BASE_URL}/v2/screenings" \
    -H "content-type: application/json" \
    -H "x-api-key: ${API_KEY}" \
    -d '{"screening_type":"onboarding","subject":{"name":"Alice Example"}}'
)"
echo "${NO_HIT}" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("decision=", d["decision"], "evidence_pack_id=", d["evidence_pack_id"])'

echo "==> Screening (v2) review: Review Example"
REVIEW="$(
  curl -sS -X POST "${BASE_URL}/v2/screenings" \
    -H "content-type: application/json" \
    -H "x-api-key: ${API_KEY}" \
    -d '{"screening_type":"onboarding","subject":{"name":"Review Example"}}'
)"
echo "${REVIEW}" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("decision=", d["decision"], "evidence_pack_id=", d["evidence_pack_id"])'

REVIEW_EVIDENCE_ID="$(echo "${REVIEW}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["evidence_pack_id"])')"
REVIEW_SCREENING_ID="$(echo "${REVIEW}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["screening_id"])')"

echo "==> Screening (v2) known-hit: John Doe"
HIT="$(
  curl -sS -X POST "${BASE_URL}/v2/screenings" \
    -H "content-type: application/json" \
    -H "x-api-key: ${API_KEY}" \
    -d '{"screening_type":"onboarding","subject":{"name":"John Doe"}}'
)"

EVIDENCE_ID="$(echo "${HIT}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["evidence_pack_id"])')"
SCREENING_ID="$(echo "${HIT}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["screening_id"])')"
DECISION="$(echo "${HIT}" | python3 -c 'import sys,json; print(json.load(sys.stdin)["decision"])')"
echo "decision=${DECISION} evidence_pack_id=${EVIDENCE_ID}"

echo "==> Recording review decision (approve) on review case"
curl -sS -X POST "${BASE_URL}/v2/screenings/${REVIEW_SCREENING_ID}/decision" \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -d '{"outcome":"approve","note":"Approved by analyst for onboarding (demo)."}' >/dev/null

echo "==> Adding case comment"
curl -sS -X POST "${BASE_URL}/v2/cases/${REVIEW_SCREENING_ID}/events" \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -d '{"event_type":"comment","note":"Investigator note recorded in case timeline (demo)."}' >/dev/null

echo "==> Assigning case to analyst-1"
curl -sS -X POST "${BASE_URL}/v2/cases/${REVIEW_SCREENING_ID}/events" \
  -H "content-type: application/json" \
  -H "x-api-key: ${API_KEY}" \
  -d '{"event_type":"assign","assignee":"analyst-1","note":"Assigned during demo workflow."}' >/dev/null

echo "==> Printing needs_review case queue"
curl -sS "${BASE_URL}/v2/cases?status=needs_review" \
  -H "x-api-key: ${API_KEY}" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("needs_review_cases=", len(d))'

echo "==> Printing analyst-1 queue"
curl -sS "${BASE_URL}/v2/cases?assignee=analyst-1" \
  -H "x-api-key: ${API_KEY}" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("assignee_cases=", len(d))'

echo "==> Fetching verifiable case bundle + verifying signature"
BUNDLE="$(
  curl -sS "${BASE_URL}/v2/cases/${REVIEW_SCREENING_ID}/bundle" \
    -H "x-api-key: ${API_KEY}"
)"
echo "${BUNDLE}" | python3 -c 'import sys,json; d=json.load(sys.stdin); print("bundle_chain_head=", d["bundle"]["chain_head"][:12], "key_id=", d["signature"]["key_id"])'
echo "${BUNDLE}" | python3 -c 'import sys,json; d=json.load(sys.stdin); print(json.dumps({"bundle": d["bundle"], "key_id": d["signature"]["key_id"], "signature": d["signature"]["signature"]}))' \
  | curl -sS -X POST "${BASE_URL}/v2/cases/bundles/verify" \
      -H "content-type: application/json" \
      -H "x-api-key: ${API_KEY}" \
      -d @- \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); assert d["valid"] is True; print("bundle_valid")'

PDF_PATH="${OUT_DIR}/evidence-pack-${REVIEW_EVIDENCE_ID}.pdf"
echo "==> Downloading evidence PDF to ${PDF_PATH}"
curl -sS -o "${PDF_PATH}" \
  -H "x-api-key: ${API_KEY}" \
  "${BASE_URL}/v2/evidence-packs/${REVIEW_EVIDENCE_ID}/export?format=pdf"

python3 -c 'import sys; p=sys.argv[1]; b=open(p,"rb").read(4); assert b==b"%PDF", f"not a pdf: {b!r}"; print("pdf_ok")' "${PDF_PATH}"
echo "==> Done."

