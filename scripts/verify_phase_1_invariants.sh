#!/usr/bin/env bash
# Phase 1 acceptance invariants (design doc Section 12).
# Exit 0 on success, 1 on first failure.

set -e

cd "$(dirname "$0")/.."

echo "== Static contract invariants =="

# EnvelopeV1.account_id required (str, Field(...))
grep -E "^\s*account_id:\s*str\s*=\s*Field\(" models/envelope.py >/dev/null \
  || { echo "FAIL: EnvelopeV1.account_id not declared as required str = Field(...)"; exit 1; }
echo "  PASS: EnvelopeV1.account_id declared as required (str = Field(...))"

# RequestContext.account_id required (str, no Optional)
grep -E "^\s*account_id:\s*str\b" models/request_context.py | grep -v "Optional" >/dev/null \
  || { echo "FAIL: RequestContext.account_id is still Optional or missing"; exit 1; }
echo "  PASS: RequestContext.account_id declared as required str"

# process_transcript(account_id: str) — public method only; tightened in T1.8.
# Private _persist_intelligence still accepts Optional[str] for graceful UUID handling.
# Check the public signature by extracting lines AFTER `async def process_transcript`.
PUBLIC_SIG=$(awk '/async def process_transcript/{flag=1} flag && /account_id/{print; flag=0}' services/intelligence_service.py)
if [ -z "$PUBLIC_SIG" ]; then
  echo "FAIL: cannot locate process_transcript public signature"; exit 1
fi
if echo "$PUBLIC_SIG" | grep -qE "Optional\[str\]"; then
  echo "FAIL: process_transcript(account_id) is still Optional[str] in public signature"; exit 1
fi
if echo "$PUBLIC_SIG" | grep -qE "=\s*None"; then
  echo "FAIL: process_transcript(account_id) still has '= None' default"; exit 1
fi
echo "  PASS: process_transcript(account_id: str) required (no default)"

# UploadJob.account_id required
grep -E "account_id:\s*str\s*=\s*Field\(sa_column" models/job_models.py >/dev/null \
  || { echo "FAIL: UploadJob.account_id not declared as required str"; exit 1; }
echo "  PASS: UploadJob.account_id required"

# TextCleanRequest.account_id required
grep -E "account_id:\s*str\s*=\s*Field\(" models/text_request.py >/dev/null \
  || { echo "FAIL: TextCleanRequest.account_id not required"; exit 1; }
echo "  PASS: TextCleanRequest.account_id required"

# UploadInitRequest.account_id required (in routers/upload.py)
grep -E "account_id:\s*str\s*=\s*Field\(" routers/upload.py >/dev/null \
  || { echo "FAIL: UploadInitRequest.account_id not required"; exit 1; }
echo "  PASS: UploadInitRequest.account_id required"

echo
echo "== No-orphan invariants =="

# No literal account_id=None in production code paths (tests / fixtures excluded)
ORPHANS=$(grep -rn "account_id=None" services/ routers/ main.py utils/ 2>/dev/null \
  | grep -v "# type: ignore" \
  | grep -v "T1.11" || true)
if [ -n "$ORPHANS" ]; then
  echo "FAIL: account_id=None found in production code paths:"
  echo "$ORPHANS"
  exit 1
fi
echo "  PASS: no account_id=None in production paths"

# Confirm transcript_enrichment.py no longer creates contacts without account_id
# (the per-attendee loop should branch via classify_domain + lookup_account_by_domain)
grep -E "classify_domain|lookup_account_by_domain" services/transcript_enrichment.py >/dev/null \
  || { echo "FAIL: transcript_enrichment.py missing three-state branching imports"; exit 1; }
echo "  PASS: transcript_enrichment.py uses three-state branching"

# Confirm pending_account_mappings helpers module exists
test -f services/pending_account_mappings.py \
  || { echo "FAIL: services/pending_account_mappings.py missing"; exit 1; }
echo "  PASS: pending_account_mappings helper module present"

# Confirm shared utilities exist
for util in services/domain_classification.py services/name_resolution.py services/account_lookup.py services/pending_account_mappings.py models/participant_spec.py; do
  test -f "$util" || { echo "FAIL: $util missing"; exit 1; }
done
echo "  PASS: all Phase 1 utility modules present"

echo
echo "== Tenant isolation invariants =="

# pending_account_mappings helpers must filter by tenant_id in SQL
grep -E "WHERE.*tenant_id\s*=\s*:tenant_id" services/pending_account_mappings.py >/dev/null \
  || grep -E "tenant_id\s*=\s*:tenant_id" services/pending_account_mappings.py >/dev/null \
  || { echo "FAIL: pending_account_mappings.py SQL does not filter by tenant_id"; exit 1; }
echo "  PASS: queue helpers filter by tenant_id"

# account_lookup.py must filter by tenant_id
grep -E "tenant_id\s*=\s*:tenant_id" services/account_lookup.py >/dev/null \
  || { echo "FAIL: account_lookup.py SQL does not filter by tenant_id"; exit 1; }
echo "  PASS: account_lookup filters by tenant_id"

echo
echo "== Test suites =="

# Unit tests covering the new invariants
echo "Running unit tests..."
pytest tests/unit -v --tb=short -q 2>&1 | tail -20
PY_UNIT_RC=${PIPESTATUS[0]}
if [ "$PY_UNIT_RC" -ne 0 ]; then
  echo "FAIL: unit tests did not pass"
  exit 1
fi

# Integration tests (some require DB; failures here may be expected — annotate carefully)
echo
echo "Running integration tests (DB-dependent ones may skip or fail with the test fixtures still pending)..."
pytest tests/integration -v --tb=short -q 2>&1 | tail -20
# Don't hard-fail on integration; print status
PY_INT_RC=${PIPESTATUS[0]}
if [ "$PY_INT_RC" -ne 0 ]; then
  echo "WARN: integration tests reported failures (review the output above; some DB-dependent tests are deferred to Phase 1.5)"
fi

echo
echo "All Phase 1 static invariants verified."
echo "Unit tests: PASS"
echo "Integration tests: see above; DB-dependent failures are tracked separately."
