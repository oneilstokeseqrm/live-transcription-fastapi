#!/usr/bin/env python3
"""
EQ-95 approvals-queue — DEV-ONLY e2e smoke (Phase D / EQ-225).

Proves the approvals workflow end to end against DEV infra, then cleans up:
  seed (unknown-domain pending_account_mapping + calendar signal, Test Tenant)
   -> GET /queue           (assert the row: contextSource='calendar', meetingTitle, attendeeCount)
   -> GET /queue/count     (assert >= 1; we never assert absolute counts — shared tenant)
   -> GET /queue/{id}      (assert status='pending', resolvedAccountId is null)
   -> POST /queue/{id}/approve   (assert 202)
   -> poll GET /queue/{id} until status='mapped' AND resolvedAccountId (<= ~300s)
   -> assert accounts + account_domains + contacts rows exist
   -> ATOMIC CLEANUP (always, in finally): FK-safe deletes incl. DBOS workflow rows.

DEV ONLY. Never run against eq-prod. Target = the dev live-transcription-fastapi
(`...-production.up.railway.app`, which is DEV per the dev/prod split) + the eq-dev
Neon DB (super-glitter-11265514). Run via `railway run` so DATABASE_URL +
INTERNAL_JWT_SECRET are injected from the dev service:

    cd <scratch-linked-to-inspiring-upliftment>
    railway run -- python /path/to/scripts/eq95_smoke.py run
    railway run -- python /path/to/scripts/eq95_smoke.py run --skip-approve   # read-path only

Env (required, injected by `railway run`): DATABASE_URL, INTERNAL_JWT_SECRET.
Optional: EQ95_BASE_URL (default the dev backend URL).
"""

import os
import sys
import time
import uuid

from urllib.parse import urlparse

import httpx
import jwt
import psycopg2

# --- DEV fixtures (Test Tenant — NOT secrets) ---
TENANT_ID = "11111111-1111-4111-8111-111111111111"
OWNER_USER_ID = "061ae392-47d5-4f04-9ea8-afa241f23555"  # users.id == JWT pg_user_id == owner_user_id
CONNECTION_ID = "74982be8-1c26-447e-a543-fd986ec4e853"  # provider_connections.id (NOT NULL FK on calendar_events)

BASE_URL = os.environ.get(
    "EQ95_BASE_URL", "https://live-transcription-fastapi-production.up.railway.app"
).rstrip("/")
MEETING_TITLE = "EQ-95 Smoke Meeting"
POLL_TIMEOUT_S = 300
POLL_INTERVAL_S = 5


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(
            f"FATAL: ${name} not set. Run via `railway run -- python scripts/eq95_smoke.py ...` "
            f"in a dir linked to the DEV project (inspiring-upliftment), so it is injected."
        )
    return val


def _safety_check(dsn: str) -> None:
    """Refuse to run against anything but the eq-dev endpoint.

    Parse the actual host (NOT a substring of the whole DSN) so a non-dev DSN
    cannot pass the guard by embedding the dev endpoint id in a password/query/db.
    """
    host = urlparse(dsn).hostname or ""
    if not host.startswith("ep-silent-waterfall-adtinpn1"):
        sys.exit(
            f"FATAL: DATABASE_URL host {host!r} is not the eq-dev endpoint "
            f"(ep-silent-waterfall-adtinpn1...). Refusing to run — this smoke is DEV ONLY."
        )


def mint_jwt(secret: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "iss": "eq-frontend",
            "aud": "eq-backend",
            "iat": now,
            "exp": now + 300,
            "tenant_id": TENANT_ID,
            "user_id": "auth0|eq95-smoke",
            "pg_user_id": OWNER_USER_ID,
        },
        secret,
        algorithm="HS256",
    )


class Run:
    """Per-run identifiers — every value is unique so concurrent runs never collide."""

    def __init__(self) -> None:
        short = uuid.uuid4().hex[:8]
        self.queue_id = str(uuid.uuid4())
        self.cal_id = str(uuid.uuid4())
        self.attempt_id = str(uuid.uuid4())
        self.domain = f"eq95-smoke-{short}.example"
        self.cemail = f"attendee-{short}@{self.domain}"
        self.workflow_id = f"queue-{self.queue_id}:approval-{self.attempt_id}"
        self.account_id: str | None = None


def seed(conn, r: Run) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO pending_account_mappings
              (id, tenant_id, domain, status, owner_user_id, discovered_from_type,
               expires_at, created_at, updated_at)
            VALUES (%s, %s, lower(%s), 'pending', %s, 'calendar',
                    NOW() + INTERVAL '30 days', NOW(), NOW())
            """,
            (r.queue_id, TENANT_ID, r.domain, OWNER_USER_ID),
        )
        cur.execute(
            """
            INSERT INTO calendar_events
              (id, tenant_id, connection_id, provider, provider_event_id,
               title, start_time, end_time, status, created_at, updated_at)
            VALUES (%s, %s, %s, 'google', %s, %s,
                    NOW() - INTERVAL '1 hour', NOW() - INTERVAL '30 minutes',
                    'confirmed', NOW(), NOW())
            """,
            (r.cal_id, TENANT_ID, CONNECTION_ID, f"eq95-smoke-{r.cal_id}", MEETING_TITLE),
        )
        cur.execute(
            """
            INSERT INTO calendar_event_attendees
              (id, calendar_event_id, tenant_id, email, display_name, is_organizer, is_resource)
            VALUES (gen_random_uuid(), %s, %s, %s, 'Smoke Attendee', true, false),
                   (gen_random_uuid(), %s, %s, %s, 'Conf Room', false, true)
            """,
            (r.cal_id, TENANT_ID, r.cemail, r.cal_id, TENANT_ID, f"room@{r.domain}"),
        )
        cur.execute(
            """
            INSERT INTO pending_account_mapping_signals
              (id, tenant_id, queue_id, source_type, source_user_id,
               interaction_id, calendar_event_id, contact_email, contact_display_name, created_at)
            VALUES (gen_random_uuid(), %s, %s, 'calendar', %s, NULL, %s, lower(%s), 'Smoke Attendee', NOW())
            """,
            (TENANT_ID, r.queue_id, OWNER_USER_ID, r.cal_id, r.cemail),
        )
    conn.commit()
    print(f"  seeded queue_id={r.queue_id} domain={r.domain}")


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def assert_read_path(client: httpx.Client, token: str, r: Run) -> None:
    # GET /queue — find OUR row by queueId (shared tenant: never assert absolute counts)
    resp = client.get(f"{BASE_URL}/queue", headers=_auth(token))
    assert resp.status_code == 200, f"GET /queue -> {resp.status_code}: {resp.text}"
    entries = resp.json()["entries"]
    mine = next((e for e in entries if e["queueId"] == r.queue_id), None)
    assert mine is not None, f"seeded row {r.queue_id} not in GET /queue ({len(entries)} entries)"
    assert mine["domain"] == r.domain, f"domain {mine['domain']} != {r.domain}"
    assert mine["contextSource"] == "calendar", f"contextSource={mine['contextSource']} (want calendar)"
    assert mine["meetingTitle"] == MEETING_TITLE, f"meetingTitle={mine['meetingTitle']!r}"
    assert mine["attendeeCount"] == 1, f"attendeeCount={mine['attendeeCount']} (want 1, resource excluded)"
    assert mine["sourceType"] == "calendar", f"sourceType={mine['sourceType']}"
    assert mine["contactCount"] == 1, f"contactCount={mine['contactCount']}"
    print(f"  GET /queue OK: contextSource=calendar, meetingTitle={mine['meetingTitle']!r}, attendeeCount=1")

    # GET /queue/count — our row contributes; never assert ==1 (shared tenant)
    resp = client.get(f"{BASE_URL}/queue/count", headers=_auth(token))
    assert resp.status_code == 200, f"GET /queue/count -> {resp.status_code}: {resp.text}"
    count = resp.json()["count"]
    assert count >= 1, f"count={count} (want >= 1)"
    print(f"  GET /queue/count OK: count={count}")

    # GET /queue/{id} — pending, not yet resolved
    resp = client.get(f"{BASE_URL}/queue/{r.queue_id}", headers=_auth(token))
    assert resp.status_code == 200, f"GET /queue/{{id}} -> {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["status"] == "pending", f"status={body['status']} (want pending)"
    assert body["resolvedAccountId"] is None, f"resolvedAccountId={body['resolvedAccountId']} (want null)"
    print(f"  GET /queue/{{id}} OK: status=pending, resolvedAccountId=null")


def approve_and_poll(client: httpx.Client, token: str, r: Run) -> bool:
    resp = client.post(
        f"{BASE_URL}/queue/{r.queue_id}/approve",
        headers=_auth(token),
        json={"approval_attempt_id": r.attempt_id},
    )
    assert resp.status_code == 202, f"POST approve -> {resp.status_code}: {resp.text}"
    print(f"  POST /queue/{{id}}/approve OK: 202 {resp.json()}")

    deadline = time.time() + POLL_TIMEOUT_S
    last = None
    while time.time() < deadline:
        resp = client.get(f"{BASE_URL}/queue/{r.queue_id}", headers=_auth(token))
        if resp.status_code == 200:
            body = resp.json()
            last = body["status"]
            if body["status"] == "mapped" and body["resolvedAccountId"]:
                r.account_id = body["resolvedAccountId"]
                elapsed = int(POLL_TIMEOUT_S - (deadline - time.time()))
                print(f"  POLL OK: status=mapped resolvedAccountId={r.account_id} (~{elapsed}s)")
                return True
        time.sleep(POLL_INTERVAL_S)
        print(f"  ...polling (status={last})", flush=True)
    print(
        f"  POLL TIMEOUT after {POLL_TIMEOUT_S}s (last status={last}). "
        f"Enrichment did not complete on the synthetic domain — approve was accepted (202) "
        f"and the workflow ran, but status never reached 'mapped'. No orphan reaper exists; "
        f"cleanup will remove the stuck row + DBOS state."
    )
    return False


def verify_account(conn, r: Run) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT account_id FROM account_domains WHERE tenant_id = %s AND domain = lower(%s)",
            (TENANT_ID, r.domain),
        )
        row = cur.fetchone()
        assert row, f"no account_domains row for {r.domain}"
        acct = str(row[0])
        if r.account_id:
            assert acct == r.account_id, f"account_domains.account_id {acct} != resolved {r.account_id}"
        cur.execute("SELECT id FROM accounts WHERE id = %s AND tenant_id = %s", (acct, TENANT_ID))
        assert cur.fetchone(), f"no accounts row {acct}"
        cur.execute(
            "SELECT id FROM contacts WHERE tenant_id = %s AND email = lower(%s)",
            (TENANT_ID, r.cemail),
        )
        assert cur.fetchone(), f"no contacts row for {r.cemail}"
    print(f"  DB verify OK: accounts + account_domains + contacts exist for {r.domain}")


def _run_delete(conn, label: str, sql: str, params: tuple) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            n = cur.rowcount
        conn.commit()
        print(f"    {label}: {n} row(s)")
    except Exception as e:  # noqa: BLE001 — best-effort teardown
        conn.rollback()
        print(f"    {label}: ERROR {type(e).__name__}: {e}")


def cleanup(conn, r: Run) -> None:
    """FK-safe, defensive teardown — runs regardless of success/failure (LOCKED-11).

    Account teardown is scoped to the per-run synthetic domain (never by raw
    account_id), and the account row is removed ONLY when it has no remaining
    domains — so a domain->existing-account collision can never delete unrelated
    account data on the shared Test Tenant.
    """
    acct = r.account_id
    with conn.cursor() as cur:
        if not acct:
            cur.execute(
                "SELECT account_id FROM account_domains WHERE tenant_id = %s AND domain = lower(%s)",
                (TENANT_ID, r.domain),
            )
            row = cur.fetchone()
            acct = str(row[0]) if row else None

    print("  cleanup:")
    for label, sql, params in [
        ("dbos.operation_outputs", "DELETE FROM dbos.operation_outputs WHERE workflow_uuid = %s", (r.workflow_id,)),
        ("dbos.workflow_status", "DELETE FROM dbos.workflow_status WHERE workflow_uuid = %s", (r.workflow_id,)),
        ("signals", "DELETE FROM pending_account_mapping_signals WHERE queue_id = %s", (r.queue_id,)),
        ("pending_account_mappings", "DELETE FROM pending_account_mappings WHERE id = %s", (r.queue_id,)),
        ("contacts", "DELETE FROM contacts WHERE tenant_id = %s AND email = lower(%s)", (TENANT_ID, r.cemail)),
        # ONLY our synthetic domain — never all domains of the account (collision-safe).
        ("account_domains", "DELETE FROM account_domains WHERE tenant_id = %s AND domain = lower(%s)", (TENANT_ID, r.domain)),
    ]:
        _run_delete(conn, label, sql, params)

    # accounts: delete ONLY if the account is now domain-less (i.e. created solely for this run).
    if acct:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM account_domains WHERE account_id = %s LIMIT 1", (acct,))
                if cur.fetchone() is not None:
                    print(f"    accounts: SKIPPED {acct} (still has other domains — not solely ours)")
                else:
                    cur.execute("DELETE FROM accounts WHERE id = %s", (acct,))
                    print(f"    accounts: {cur.rowcount} row(s)")
            conn.commit()
        except Exception as e:  # noqa: BLE001
            conn.rollback()
            print(f"    accounts: ERROR {type(e).__name__}: {e}")

    for label, sql, params in [
        ("calendar_event_attendees", "DELETE FROM calendar_event_attendees WHERE calendar_event_id = %s", (r.cal_id,)),
        ("calendar_events", "DELETE FROM calendar_events WHERE id = %s", (r.cal_id,)),
    ]:
        _run_delete(conn, label, sql, params)

    _verify_clean(conn, r)


def _verify_clean(conn, r: Run) -> None:
    """Re-check no fixture residue remains (e.g. if the workflow was still in-flight
    on a timeout path and re-created rows after teardown). Surfaces residue loudly
    instead of silently reporting a clean teardown."""
    checks = [
        ("pending_account_mappings", "SELECT 1 FROM pending_account_mappings WHERE id = %s", (r.queue_id,)),
        ("signals", "SELECT 1 FROM pending_account_mapping_signals WHERE queue_id = %s", (r.queue_id,)),
        ("calendar_events", "SELECT 1 FROM calendar_events WHERE id = %s", (r.cal_id,)),
        ("account_domains", "SELECT 1 FROM account_domains WHERE tenant_id = %s AND domain = lower(%s)", (TENANT_ID, r.domain)),
        ("contacts", "SELECT 1 FROM contacts WHERE tenant_id = %s AND email = lower(%s)", (TENANT_ID, r.cemail)),
    ]
    leftovers = []
    with conn.cursor() as cur:
        for label, sql, params in checks:
            cur.execute(sql, params)
            if cur.fetchone():
                leftovers.append(label)
    if leftovers:
        print(
            f"  ⚠️ RESIDUE REMAINS: {leftovers} — the provisioning workflow may have still been "
            f"in-flight. Re-run cleanup or inspect domain={r.domain}, queue_id={r.queue_id}."
        )
    else:
        print("  cleanup verified: zero residue.")


def main() -> None:
    skip_approve = "--skip-approve" in sys.argv
    cmd = next((a for a in sys.argv[1:] if not a.startswith("-")), None)
    if cmd != "run":
        print(__doc__)
        sys.exit(1)

    dsn = _require_env("DATABASE_URL")
    secret = _require_env("INTERNAL_JWT_SECRET")
    _safety_check(dsn)

    r = Run()
    token = mint_jwt(secret)
    conn = psycopg2.connect(dsn)
    ok = True
    try:
        print(f"EQ-95 smoke against {BASE_URL}")
        print("seeding fixture...")
        seed(conn, r)
        with httpx.Client(timeout=30.0) as client:
            assert_read_path(client, token, r)
            if skip_approve:
                print("  --skip-approve: read path verified; skipping approve/poll.")
            else:
                mapped = approve_and_poll(client, token, r)
                if mapped:
                    verify_account(conn, r)
                else:
                    ok = False
        print("\nSMOKE RESULT:", "PASS" if ok else "PARTIAL (read+approve OK; enrich did not complete)")
    except Exception as e:  # noqa: BLE001
        ok = False
        print(f"\nSMOKE RESULT: FAIL — {type(e).__name__}: {e}")
    finally:
        cleanup(conn, r)
        conn.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
