# Next Session — Start Here

**Project:** Phase 2 — Granola.ai transcript ingestion integration.
**Last session:** 2026-05-22 (brainstorm closed; plan locked + reviewed via /plan-eng-review and /codex consult; build next).
**Status:** ✅ BRAINSTORM_COMPLETE_PLAN_LOCKED_REVIEWED. Build session is the next step.

**Review surfaced 22 LOCKED decisions (LOCKED-23 through LOCKED-44).** Of those, 6 came from the outside-voice Codex review and would have caused build-time bugs:
- LOCKED-39: `@DBOS.scheduled` is deprecated per repo's own DBOS plan; use external Railway cron
- LOCKED-40: KMS EncryptionContext must bind all 4 fields (was 2; tenant-internal row-swap gap)
- LOCKED-41: Extract `/text/clean` core; don't HTTP-call from adapter
- LOCKED-42: Single Postgres engine for MVP; second role + engine = Phase 2.1
- LOCKED-43: Fresh DEK + fresh nonce on every encrypted_api_key write
- LOCKED-44: Capture `granola_note_snapshot` at defer time for recoverability

---

## START BY READING THIS DOC

**Load-bearing executable plan:** `tasks/granola-integration-plan.md`

That plan is comprehensive (~660 lines). It contains:
- All 16 new LOCKED decisions (LOCKED-23 through LOCKED-38)
- 6 build phases (Phase 0 pre-flight → Phase 1 AWS → Phase 2 backend → Phase 3 frontend → Phase 4 testing → Phase 5 Codex gate → Phase 6 deploy)
- 13 test scenarios mapped to Q7 failure modes
- Phase-by-phase exit criteria
- Pre-merge checklist
- Cross-cutting constraint citations (Codex pre-merge gate, verify_consumer_contracts.py, tenant isolation, etc.)
- Build session entry prompt (paste at the end)

**Background brainstorm doc (don't re-litigate):** `docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md` (~600 lines).

Read the plan first, then the brainstorm doc only if you need to understand WHY a decision was made.

---

## SESSION SCOPE — BUILD

| Phase | Time | Description |
|---|---|---|
| Phase 0 | ~0.5 day | Pre-flight verification: scripts/verify_consumer_contracts.py against proposed envelope (source="generic", interaction_type="meeting"). Fall back to source="api" if drift. |
| Phase 1 | ~0.5 day | AWS infrastructure: KMS CMK `alias/eq-user-secrets` + IAM user `eq-vault-service` + Railway env vars |
| Phase 2 | ~4-5 days | Backend: Prisma migrations (eq-frontend) → vault module → Granola API client → adapter (Path 2) → DBOS scheduler → admin endpoints → transactional email |
| Phase 3 | ~2 days | Frontend (eq-frontend): Granola Connect settings page + EQ-native Pending Approvals component |
| Phase 4 | ~1 day | Testing: unit + integration + production E2E on test tenant |
| Phase 5 | varies | Pre-merge Codex review gate (4-round soft cap, extend if real P1s persist) |
| Phase 6 | ~0.5 day | Deploy + verify |

**Estimated total:** ~6-7 days of focused engineering, likely 3-5 build sessions.

---

## CRITICAL — WHAT NOT TO RELITIGATE

The plan's "LOCKED Decisions" table is non-negotiable without strong new information. The top constraints:

1. **`interaction_type="meeting"`** — anything else trips the `raw_interactions` FK landmine. Verified live in `routers/text.py:80-95`.
2. **`source="generic"`** with verification gate — fits existing downstream enum; do NOT add "granola" to action-item-graph or eq-structured-graph-core. See [[feedback-envelope-contract-immutable]] memory.
3. **Path 2 (defer + re-poll)** for account resolution — not Path 1 (pending_transcripts table).
4. **AWS KMS envelope encryption + `vault.user_credentials`** — not env vars, not Secrets Manager.
5. **Per-user (not org-scoped) credentials** — Granola's API model is 1 key per user.
6. **Snapshot-on-ingest** — no reverse-sync on Granola edits/deletes/folder-moves.
7. **Soft-delete on disconnect** — preserves audit trail.
8. **No Docker in tests** — AsyncMock unit tests + production E2E on test tenant (per [[test-pattern-no-docker-default]]).

---

## OPEN ITEMS NOT IN MVP

Deferred but tracked in the plan's "Post-implementation follow-ups" section:

1. **Alerting wire-up (Phase 2.1)** — Slack webhook OR Resend for critical conditions (KMS failure, adapter not running). User-deferred from Q7.
2. **Org-admin bulk-onboarding (Phase 3)** — when >10 users.
3. **Reverse-sync** — only if user feedback demands it; LOCKED-27 says no for MVP.
4. **Webhooks instead of polling** — when Granola releases webhooks.
5. **Other Phase 2 backlog candidates** (Neo4j MERGE-everywhere, contact identity state machine, etc.) — independent initiatives.

---

## AWS ACCOUNT STATE (verified 2026-05-22, unchanged)

- Account ID: `211125681610`
- Root: MFA on, no access keys
- IAM user `peter-admin-cli` with AdministratorAccess (used by CLI + AWS MCP)
- 8 KMS keys exist (7 AWS-managed for services + 1 unused custom from June 2024)
- Build will add: KMS CMK aliased `alias/eq-user-secrets` + IAM user `eq-vault-service`

---

## DESIGN PARTNER CONTEXT (unchanged)

- 3 design partners on Granola Business plan
- Peter (you) has personal Business plan account = design partner #0 for testing
- Granola Personal API released late March 2026, included in Business plan
- Each user generates their own `grn_…` API key + creates a designated "EQ" folder
- Empirical API validation done 2026-05-21 against Peter's real account

---

## TASK LIST STATE AT END OF LAST SESSION (2026-05-22)

| # | Status | Subject |
|---|---|---|
| 1 | completed | Walk through Q6 — Granola Connect settings page UX |
| 2 | completed | Walk through Q7 — error handling |
| 3 | completed | Walk through Q8 — envelope labels |
| 4 | completed | Write tasks/granola-integration-plan.md |
| 5 | optional | Plan reviews (/plan-eng-review and/or /codex consult) |
| 6 | pending | End session — commit plan + memory update |

---

## PHASE 1 STATUS (REFERENCE)

Phase 1 email pipeline initiative: ✅ COMPLETE 2026-05-18. All 8 milestones shipped + verified end-to-end. 22 LOCKED decisions captured. Production stable.

Granola integration is Phase 2 work — the first concrete initiative after Phase 1 closed. Prior Phase 1 session-handoff content has been superseded by this file (Phase 1 docs live in git history).

---

## BUILD SESSION ENTRY PROMPT

Paste this block as the opening message of the next session:

```
You're starting the Granola integration build. The brainstorm is closed; the plan is locked.

START BY READING:
1. tasks/granola-integration-plan.md (executable plan) — top-to-bottom
2. docs/superpowers/specs/2026-05-22-granola-integration-brainstorm-decisions.md (background; don't re-litigate)
3. ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/MEMORY.md (auto-loaded)
4. ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/project_granola_integration.md (status snapshot)
5. ~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/feedback_envelope_contract_immutable.md (load-bearing rule)
6. tasks/lessons.md (1820 lines of cross-cutting discipline)

Then execute Phase 0 (pre-flight verification) to confirm source="generic" works downstream. If verify_consumer_contracts.py surfaces drift, fall back to source="api" and update LOCKED-35 in the plan; do NOT modify downstream.

Then execute Phase 1 (AWS) → Phase 2 (backend) → Phase 3 (frontend) → Phase 4 (testing) sequentially.

Codex pre-merge gate is mandatory; 4-round soft cap.

Coordinate with user before:
- Running any destructive SQL on the test tenant
- Merging the eq-frontend Prisma migration PR (cross-repo coordination)
- Any decision that deviates from the locked plan

User posture: non-developer founder; plain-English explanations; investigate thoroughly; no shortcuts.
```
