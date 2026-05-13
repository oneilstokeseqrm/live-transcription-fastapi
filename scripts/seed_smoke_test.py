#!/usr/bin/env python3
"""
E2E Smoke Test: Seed Meeting Transcripts through Live Pipeline

Reads the 37 most recent meeting transcripts from the eq-seed-test-data SQLite DB
and POSTs them through the deployed Railway /text/clean endpoint with JWT auth.

Strategy: Run 2 canary transcripts first, verify in Neon, then run remaining 35.

Usage:
    python3 scripts/seed_smoke_test.py canary     # Run 2 canary transcripts
    python3 scripts/seed_smoke_test.py verify      # Verify results in Neon
    python3 scripts/seed_smoke_test.py batch       # Run remaining 35
    python3 scripts/seed_smoke_test.py final       # Final verification
"""

import os
import sqlite3
import sys
import time

# Unbuffered stdout
os.environ["PYTHONUNBUFFERED"] = "1"

import httpx
import jwt
import psycopg2

# --- Configuration ---

SQLITE_DB = "/Users/peteroneil/eq-seed-test-data/seed_meetings.db"
RAILWAY_URL = "https://live-transcription-fastapi-production.up.railway.app/text/clean"
NEON_DSN = "postgresql://neondb_owner:npg_leZSs0cA1zIp@ep-silent-waterfall-adtinpn1-pooler.c-2.us-east-1.aws.neon.tech/neondb?sslmode=require"

JWT_SECRET = "gUJE4jag11MO9+sTGBeBJB30EWganLlBeG3JPB45BmA="
TENANT_ID = "11111111-1111-4111-8111-111111111111"

# 37 most recent meeting IDs (by date DESC)
ALL_IDS = [
    342, 341, 340, 339, 338, 337, 336, 335, 334, 333, 332, 331,
    330, 329, 328, 327, 326, 325, 324, 323, 322, 321, 320, 319,
    318, 317, 316, 315, 314, 313, 311, 310, 309, 308, 307, 306, 305,
]
CANARY_IDS = [341, 339]
BATCH_IDS = [mid for mid in ALL_IDS if mid not in CANARY_IDS]

DELAY_BETWEEN_REQUESTS = 2  # seconds


def mint_jwt() -> str:
    """Mint a fresh JWT mimicking eq-frontend."""
    now = int(time.time())
    payload = {
        "tenant_id": TENANT_ID,
        "user_id": "auth0|seed-smoke-test",
        "user_name": "Seed Smoke Test",
        "iss": "eq-frontend",
        "aud": "eq-backend",
        "iat": now,
        "exp": now + 300,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def get_meeting(db_path: str, meeting_id: int) -> dict:
    """Read a single meeting from SQLite."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT id, date, full_transcript, customer_name, account_id "
        "FROM meetings WHERE id = ?",
        (meeting_id,),
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        raise ValueError(f"Meeting ID {meeting_id} not found")
    return dict(row)


def post_transcript(meeting: dict) -> dict:
    """POST a single transcript to Railway /text/clean."""
    token = mint_jwt()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    # Add X-Account-ID header if meeting has an account_id
    if meeting.get("account_id"):
        headers["X-Account-ID"] = meeting["account_id"]

    body = {
        "text": meeting["full_transcript"],
        "source": "seed-smoke-test",
        "interaction_type": "meeting",
        "metadata": {
            "seed_meeting_id": meeting["id"],
            "customer_name": meeting.get("customer_name"),
            "meeting_date": meeting.get("date"),
        },
    }

    with httpx.Client(timeout=600.0) as client:
        resp = client.post(RAILWAY_URL, json=body, headers=headers)

    return {
        "status_code": resp.status_code,
        "body": resp.json() if resp.status_code == 200 else resp.text,
    }


def run_batch(meeting_ids: list[int], label: str):
    """Process a list of meeting IDs sequentially."""
    print(f"\n{'=' * 60}")
    print(f"  {label}: {len(meeting_ids)} transcripts")
    print(f"{'=' * 60}\n")

    results = []
    for i, mid in enumerate(meeting_ids, 1):
        meeting = get_meeting(SQLITE_DB, mid)
        chars = len(meeting["full_transcript"])
        acct = meeting.get("account_id") or "(none)"
        cust = meeting.get("customer_name") or "(internal)"

        print(f"[{i}/{len(meeting_ids)}] ID={mid}  chars={chars:,}  "
              f"customer={cust}  account={acct}", flush=True)

        try:
            result = post_transcript(meeting)
            if result["status_code"] == 200:
                iid = result["body"]["interaction_id"]
                print(f"  -> 200 OK  interaction_id={iid}", flush=True)
                results.append({"id": mid, "interaction_id": iid, "ok": True})
            else:
                print(f"  -> {result['status_code']} FAIL: {result['body'][:200]}", flush=True)
                results.append({"id": mid, "error": result["body"][:200], "ok": False})
        except Exception as e:
            print(f"  -> EXCEPTION: {type(e).__name__}: {e}", flush=True)
            results.append({"id": mid, "error": str(e), "ok": False})

        if i < len(meeting_ids):
            time.sleep(DELAY_BETWEEN_REQUESTS)

    # Summary
    ok = sum(1 for r in results if r["ok"])
    fail = len(results) - ok
    print(f"\n{'=' * 60}")
    print(f"  {label} complete: {ok} succeeded, {fail} failed")
    print(f"{'=' * 60}")

    if fail:
        print("\nFailed:")
        for r in results:
            if not r["ok"]:
                print(f"  ID={r['id']}: {r.get('error', 'unknown')}")

    return results


def verify_neon(label: str):
    """Run verification queries against Neon Postgres."""
    print(f"\n{'=' * 60}")
    print(f"  {label}: Neon Postgres Verification")
    print(f"{'=' * 60}\n")

    conn = psycopg2.connect(NEON_DSN)
    cur = conn.cursor()

    queries = [
        (
            "Distinct interactions (meeting type, today)",
            """SELECT COUNT(DISTINCT interaction_id) FROM interaction_summary_entries
               WHERE interaction_type = 'meeting' AND created_at > '2026-02-27'""",
        ),
        (
            "Summary rows (should be interactions × 5)",
            """SELECT COUNT(*) FROM interaction_summary_entries
               WHERE interaction_type = 'meeting' AND created_at > '2026-02-27'""",
        ),
        (
            "Insights breakdown by type",
            """SELECT type, COUNT(*) FROM interaction_insights
               WHERE interaction_type = 'meeting' AND created_at > '2026-02-27'
               GROUP BY type ORDER BY count DESC""",
        ),
        (
            "Interactions with account_id",
            """SELECT COUNT(DISTINCT interaction_id) FROM interaction_summary_entries
               WHERE interaction_type = 'meeting'
               AND account_id IS NOT NULL
               AND created_at > '2026-02-27'""",
        ),
        (
            "Sample: latest 5 interactions",
            """SELECT interaction_id, account_id, created_at
               FROM interaction_summary_entries
               WHERE interaction_type = 'meeting' AND created_at > '2026-02-27'
               ORDER BY created_at DESC LIMIT 5""",
        ),
    ]

    for title, sql in queries:
        print(f"--- {title} ---")
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            for row in rows:
                print(f"  {row}")
        except Exception as e:
            print(f"  ERROR: {e}")
            conn.rollback()
        print()

    cur.close()
    conn.close()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1].lower()

    if cmd == "canary":
        run_batch(CANARY_IDS, "CANARY (2 transcripts)")

    elif cmd == "verify":
        verify_neon("CANARY VERIFICATION")

    elif cmd == "batch":
        run_batch(BATCH_IDS, "BATCH (35 transcripts)")

    elif cmd == "all":
        run_batch(ALL_IDS, "FULL BATCH (37 transcripts)")

    elif cmd == "resume":
        # Resume from a given meeting ID onward
        if len(sys.argv) < 3:
            print("Usage: seed_smoke_test.py resume <start_id>")
            sys.exit(1)
        start_id = int(sys.argv[2])
        remaining = [mid for mid in ALL_IDS if mid <= start_id]
        run_batch(remaining, f"RESUME (from ID {start_id}, {len(remaining)} transcripts)")

    elif cmd == "final":
        verify_neon("FINAL VERIFICATION")

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
