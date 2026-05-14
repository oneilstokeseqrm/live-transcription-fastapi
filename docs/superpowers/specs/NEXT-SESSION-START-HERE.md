# Next Session — Start Here

**Project:** Contact Quality and Account-Anchoring Initiative
**Last session:** 2026-05-13 (Phase 1 implementation + Codex review)
**Status:** Phase 1 IMPLEMENTED but `/codex review` returned **GATE: FAIL — 3 P1 findings**. PR #10 (live-transcription-fastapi) must NOT merge until P1s close. PR #6 (eq-email-pipeline) is independently shippable. eq-frontend PR #349 already merged.
**This session's job:** Close the 3 P1s (and optionally the 3 P2s), re-run `/codex review`, ship Phase 1.

---

## Critical context (READ FIRST, BEFORE EVERYTHING ELSE)

Four things you must internalize before opening any file:

1. **You are NOT starting from scratch.** Phase 1 is ~95% implemented on branch `feat/contact-quality-phase-1` (~26 commits ahead of `main`). What's missing is small but blocking — six specific fixes Codex flagged. The implementation itself is sound; the issue is integration-completeness gaps that a static self-review missed.

2. **The Codex review IS the redirect signal.** The prior session's orchestrator (me) produced a structural self-review that concluded "0 CRITICAL, 0 IMPORTANT." Running real `/codex review` then surfaced three P1s. The lesson: **the recurring quality gate in design Section 8.4 is non-substitutable.** Always run real Codex at every phase boundary, not a Codex-format self-review.

3. **The 3 P1s break Phase 1's stated guarantees.** Not theoretical issues:
   - **P1 #1:** the queue feature (unknown-domain attendees → signal rows) is **literally unreachable in production** because none of the four ingress routes pass `recording_user_id` to `enrich()`. Real traffic silently drops these attendees.
   - **P1 #2 + #3:** body-supplied `account_id` can override the authenticated `X-Account-ID` header. A mismatched request persists under the **wrong account** despite passing the new auth check. This defeats the entire "backend rejection over frontend trust" invariant.

4. **The user is a non-developer founder.** Make confident technical calls on fix sequencing, dispatch prompts, and review judgments. Surface only product/strategic decisions. The user explicitly said: "Make the reasonable call and continue; they'll redirect if needed."

---

## Read these in order before doing any work

Total reading: ~20-30 minutes. Most of it is reading what already shipped + the new Codex findings doc.

1. **Auto-loaded:** `MEMORY.md` (you should see it as `PHASE_1_PENDING_CODEX_FIXES`)

2. **Project status + decision log:** `~/.claude/projects/-Users-peteroneil-EQ-CORE-live-transcription-fastapi/memory/project_contact_quality_initiative.md` — the `## Phase 1 ship (2026-05-13)` section captures what landed; the `## Codex round-1 findings (2026-05-13)` section captures what's now blocking.

3. **The Codex findings doc — your execution plan:** `tasks/downstream/codex-phase-1-findings.md`. **READ THIS FIRST AMONG THE PROJECT FILES.** It contains the verbatim Codex findings plus per-task fix specs for Tasks 1.26.1 through 1.26.6.

4. **The prior session's self-review (audit trail, NOT action items):** `tasks/downstream/codex-phase-1-review.md`. Understand what the prior reviewer claimed; understand why Codex disagreed. This is a lesson for Phase 1.5's T1.5.23 — don't substitute self-review for real Codex.

5. **The design document (canonical project intent):** `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md` — header reads "Design approved." Sections 3.2 (backend rejection), 5.2-5.3 (queue UPSERT + owner determination), 7.1 (Phase 1 scope), 12 (verifiable invariants) are the most-referenced.

6. **The implementation plan:** `docs/superpowers/plans/2026-05-13-contact-quality-phase-1-and-1.5.md`. The Tasks 1.26.1-1.26.6 in the findings doc are inserts into this plan's T1.26 step. Phase 1.5 starts at line ~2538.

7. **Codex review historical audit trail (do NOT re-integrate):** `docs/superpowers/specs/2026-05-12-contact-quality-initiative-codex-review.md` — the 15 findings from the design-doc review. All integrated into the design. The new 6 findings are SEPARATE from these.

8. **Architecture reference:** `docs/contacts-architecture.md` — Section 3.4 documents the three-state branching contract Phase 1 introduced.

9. **Cross-repo state — both PRs:**
   - **eq-frontend PR #349** — MERGED. Phase 1 schema is live on Neon eq-dev. No action needed.
   - **eq-email-pipeline PR #6** — OPEN, ready for merge. The cross-repo agent in the prior session wired callers correctly there (its callers `fetch.py` and `retry.py` already supply `connected_user_id` and `internal_domains`). **PR #6 is independently mergeable.** It is NOT affected by the live-transcription-fastapi caller-wiring bugs.
   - **live-transcription-fastapi PR #10** — OPEN. Has 26 commits including the Phase 1 implementation + 4 commits of pre-codex polish. **DO NOT MERGE** until the P1s close + Codex Round 2 passes.

---

## What this session does

Execute the fixes in `tasks/downstream/codex-phase-1-findings.md` then ship Phase 1.

### Step 1 — Invoke the sub-skill

Before dispatching the first task, invoke `superpowers:subagent-driven-development`. The canonical orchestrator-subagent workflow.

### Step 2 — Walk Tasks 1.26.1 through 1.26.6 in order

Critical ordering:

```
T1.26.1 (wire recording_user_id + tenant_internal_domains; raise-not-skip on None)
  → T1.26.2 (text/clean reject account_id mismatch)
    → T1.26.3 (upload/init reject account_id mismatch)
      → (Optional) T1.26.4 (get_auth_context: optional account_id for polling)
        → (Optional) T1.26.5 (UploadJob.participants_json through worker) ← cross-repo eq-frontend Prisma change
          → (Optional) T1.26.6 (text/clean honor body.participants)
            → T1.26.7 (re-run /codex review)
              → T1.26.8 (re-run scripts/verify_phase_1_invariants.sh + pytest)
                → T1.26.9 (update PR #10 + memory)
                  → T1.26.10 (merge PR #6 + PR #10, /canary, /document-release)
```

The P1s (1.26.1, 1.26.2, 1.26.3) are non-optional. The P2s (1.26.4, 1.26.5, 1.26.6) can defer to Phase 1.5 if context is tight — but doing them now is the right call if budget allows because they're small.

### Step 3 — Re-run `/codex review` (T1.26.7 — non-substitutable)

Once the P1 fixes are in, run `/codex review --base main` again. Expected outcome: GATE: PASS, 0 P1 findings. If new P1s surface, address them and re-run. **Do not proceed to merge with any open P1.**

The Codex CLI gotcha you'll hit: `codex review --base main` is mutually exclusive with passing a custom prompt argument. Use `codex review --base main -c 'model_reasoning_effort="high"' --enable web_search_cached < /dev/null 2>&1` and let Codex auto-build the review.

### Step 4 — Run the full Phase 1 verification suite (T1.26.8)

```bash
./scripts/verify_phase_1_invariants.sh
pytest tests/ -v --tb=short
```

All 12 static invariants must pass. All 122 unit tests + the integration tests (30 prior + new tests added for the fixes) must pass.

### Step 5 — Update PR #10 + memory (T1.26.9)

- Comment on PR #10 with the Codex Round 2 verdict + a link to the round-2 review log.
- Append `## Codex round-2 results (YYYY-MM-DD)` to `tasks/downstream/codex-phase-1-review.md`.
- Update auto-memory project file status from `PHASE_1_PENDING_CODEX_FIXES` to `PHASE_1_READY_TO_SHIP`.

### Step 6 — Merge PR #6 first, then PR #10 (T1.26.10)

```bash
# Merge eq-email-pipeline PR #6 first (independent of these fixes)
gh pr merge 6 --repo oneilstokeseqrm/eq-email-pipeline --squash --delete-branch

# Then merge live-transcription-fastapi PR #10
gh pr merge 10 --repo oneilstokeseqrm/live-transcription-fastapi --squash --delete-branch
```

### Step 7 — Verify Railway auto-deploy

Use `/canary` to monitor the deploy. Check that production health is green within 5-10 minutes of the merge. Railway watches `main` on live-transcription-fastapi.

### Step 8 — `/document-release` to sync post-ship docs

Run `/document-release` — it'll diff what shipped against `README.md`, `docs/contacts-architecture.md`, `CLAUDE.md`, `CHANGELOG.md` and apply doc updates to match.

### Step 9 — Update auto-memory + rewrite this handoff for Phase 1.5

After Phase 1 ships:
- Update `MEMORY.md` index pointer status to `PHASE_1_SHIPPED_PHASE_1_5_PENDING`
- Update `project_contact_quality_initiative.md` with a `## Phase 1 shipped (YYYY-MM-DD)` section
- **Rewrite `docs/superpowers/specs/NEXT-SESSION-START-HERE.md`** to point at Phase 1.5 work. Use this file's structure as the template.

### Step 10 — Hand off

Phase 1.5 is its own session. Do NOT start Phase 1.5 in this session unless context is genuinely fresh AND the fixes were trivial AND the day is young. The honest expected scope of this session is "fix P1s + ship Phase 1 + handoff for Phase 1.5."

---

## Subagent dispatch best practices (carried forward from the prior handoff)

Each subagent dispatch must include:

1. **The task block from `codex-phase-1-findings.md` in full.** Copy the entire `### Task 1.26.X` section including the Codex finding, root cause, files-to-change, and TDD steps.

2. **Required prior context.** Links to the design doc + Codex findings doc + a one-line summary of which prior commits established the invariant being protected.

3. **Boundaries explicit.** What the subagent should NOT do: invent new schema fields, refactor adjacent unrelated code, skip TDD red→green→commit, deviate from the fix spec.

4. **Acceptance evidence required.** Exact commands run, test output proving red→green, commit hash, `git diff HEAD~1 --stat`.

5. **TDD discipline.** Each fix has a failing test that demonstrates the bug exists today, then becomes green after the fix. No shortcuts.

### Reviewing subagent output

Same pattern as the prior session:
- Verify commit exists in `git log`
- Verify diff matches what the fix spec asked for
- Run the test command yourself when the diff is non-trivial
- If output conflicts with the spec, ask before accepting

---

## Critical project invariants (must hold across all fixes)

All from the prior handoff, still in force, plus two new ones from the Codex findings:

- **Contact ID consistency:** every contact carries UUIDv4 `contact_id`. Never store a name without an ID.
- **Tenant isolation:** every Postgres + Neo4j query MUST include `tenant_id`. Never cross-tenant queries.
- **Three-state branching:** known account → contact; unknown business domain → queue signal, no contact; personal/internal → skip. NEVER fall back to anchor.
- **Backend rejection over frontend trust:** every ingestion path validates `account_id` at the auth-context boundary or 400s (WebSocket: 1008). Queue-hold path is the ONLY exemption.
- **First-owner-wins UPSERT:** `pending_account_mappings.owner_user_id` is never reassigned by routine UPSERT.
- **Outbox-backed durability** (Phase 1.5): `account_provisioning_outbox` written in same Postgres txn as account materialization.
- **Codex usage as recurring quality gate:** real `/codex review`, NOT a self-review in Codex format. **This session is the proof point.**

**New invariants surfaced by the Codex findings:**

- **Caller-side completeness:** when adding a new parameter to an internal function, immediately update every caller. A "wire callers in Phase X.5" deferral is a silent-failure bomb — unit tests pass but production traffic never reaches the new code path.
- **Auth boundary wins on conflict:** if a request body field has the same semantic as an authenticated header value, the auth header wins. The body field is at best a verification check, at worst a security regression. Default to: reject mismatch with 400.

---

## Repository state (as of 2026-05-13 Codex-review session end)

- **Current branch (unmerged):** `feat/contact-quality-phase-1` — ~26 commits ahead of main. Pushed to origin.
- **Phase 1 PRs:**
  - eq-frontend PR #349 — ✅ MERGED. Schema live on Neon eq-dev.
  - eq-email-pipeline PR #6 — 🟢 OPEN, ready. 43 directly-relevant tests pass. Independent of the live-transcription-fastapi P1 fixes.
  - live-transcription-fastapi PR #10 — 🔴 OPEN, BLOCKED on Codex P1 fixes. https://github.com/oneilstokeseqrm/live-transcription-fastapi/pull/10
- **Verification artifacts as of session end:**
  - `scripts/verify_phase_1_invariants.sh` — exit 0 (all 12 static invariants PASS)
  - `tasks/downstream/codex-phase-1-review.md` — self-review (0 critical, missed real findings)
  - `tasks/downstream/codex-phase-1-findings.md` — **THE EXECUTION PLAN for this session**
- **Test results:**
  - Unit tests: 122 PASS
  - Integration tests: 30 PASS, 1 intentional skip
- **Test tenant ID:** `11111111-1111-4111-8111-111111111111`
- **Neon Postgres:** project `super-glitter-11265514` (eq-dev). Phase 1 schema applied.
- **Cross-repo paths:**
  - `/Users/peteroneil/eq-frontend` — schema, optional Prisma migration for T1.26.5
  - `/Users/peteroneil/eq-email-pipeline` — PR #6 ready (no action)
- **PR #10 needs a comment** noting Codex findings before another reviewer touches it — see step "PR comment" below.

---

## What NOT to do this session

- Do NOT merge PR #10 until Codex Round 2 returns GATE: PASS with 0 P1 findings.
- Do NOT substitute a self-review for real `/codex review`. The prior session did this and missed 3 P1s.
- Do NOT skip the TDD red→green→commit cycle on any fix. Each Codex finding has a specific behavior that should fail today and pass after the fix.
- Do NOT defer P1 fixes to Phase 1.5. They are blockers, not nits.
- Do NOT change locked schema or design decisions to "work around" a finding. The fixes named in `codex-phase-1-findings.md` are the correct shape — they don't require design re-litigation.
- Do NOT modify locked invariants. The auth-boundary-wins resolution for findings #2 and #3 is the correct application of design Section 3.2's "backend rejection over frontend trust."
- Do NOT skip the cross-repo Prisma migration for T1.26.5 (P2) if you choose to do it. `UploadJob.participants_json` needs a real schema column.
- Do NOT commit on any branch other than `feat/contact-quality-phase-1` (the existing Phase 1 branch). The fixes are continuations of the Phase 1 PR, not a new branch.
- Do NOT use `--no-verify` on any commit. Pre-commit hooks exist for reasons.

---

## Context budget guidance

This session is much smaller scope than the implementation session:

- **P1 fixes only (1.26.1 + 1.26.2 + 1.26.3) + Codex Round 2 + merge + canary + docs:** Plausible in one session. Each P1 fix is 1-10 lines plus a TDD pair. Codex round-trip costs context; budget accordingly.
- **P1s + P2s (all six) + merge:** Tighter, especially if T1.26.5 requires a cross-repo Prisma migration round-trip. Possible but the P2s could defer.
- **Phase 1.5 in this same session:** Unlikely. Plan to hand off after Phase 1 ships.

**Signs to stop and hand off:**
- Codex Round 2 still returns P1s (recovery work is non-trivial)
- You're about to invoke another heavy-context skill mid-execution (e.g., `/codex challenge`)
- An acceptance gate fails and recovery requires deep design re-engagement
- Context approaching limits

When you stop, do a clean handoff: update auto-memory + rewrite this NEXT-SESSION-START-HERE.md + commit the handoff changes. **This file is your template for what a good handoff looks like.**

---

## PR comment (do this early in the session)

Before starting fixes, post a comment on PR #10 so any human reviewer doesn't merge prematurely:

```bash
gh pr comment 10 --repo oneilstokeseqrm/live-transcription-fastapi --body "$(cat <<'EOF'
## Codex review (2026-05-13) — GATE: FAIL

3 × P1 findings (see \`tasks/downstream/codex-phase-1-findings.md\` for full text + fix specs):

1. **Queue feature unreachable from real traffic** — \`recording_user_id\` not threaded through ingress routes; unknown-domain attendees silently dropped instead of producing queue signals.
2. **\`/text/clean\` ignores authenticated \`X-Account-ID\`** — body.account_id wins over the auth header, defeating backend-rejection enforcement.
3. **\`/upload/init\` persists body.account_id on UploadJob** — same auth-bypass pattern.

Plus 3 × P2 (participants persistence + non-ingestion auth context).

**Do not merge yet.** Fixes are being addressed as Tasks 1.26.1 through 1.26.6 in a follow-up session.

See \`tasks/downstream/codex-phase-1-findings.md\` for the canonical fix plan.
EOF
)"
```

---

## If something doesn't make sense

The conversation that produced these artifacts ran across multiple sessions. If you encounter a contradiction:

1. **First, read `tasks/downstream/codex-phase-1-findings.md`.** It's the most recent and authoritative source for what's blocking.
2. **Second, check the auto-memory `project_contact_quality_initiative.md` Decision log.** Locked decisions take precedence over inferences from code.
3. **Third, check the design doc.** Section 12 (verifiable invariants) is the contract.
4. **Fourth, ask the user.** Surface with clear ELI10 framing. Default to asking when product/strategic; default to deciding when implementation detail.

If a subagent's output conflicts with a Codex finding's fix spec, the fix spec wins (the spec is what Codex asked for; deviating from it means the fix is incomplete).

---

## Suggested first actions for the next agent

1. Run `/context-restore` (gstack skill) to load any checkpoint state.
2. Read this doc in full.
3. Read `MEMORY.md`, `project_contact_quality_initiative.md`, `tasks/downstream/codex-phase-1-findings.md`, in that order. Codex review and self-review docs are skim-only (history).
4. Briefly confirm understanding back to the user (one paragraph).
5. Comment on PR #10 with the Codex findings (the bash snippet above).
6. Invoke `superpowers:subagent-driven-development` for the canonical orchestration workflow.
7. Dispatch the first subagent for Task 1.26.1 (wire `recording_user_id`). This is the biggest of the P1s — fix it first, the others will reveal whether the pattern is consistent across the file changes.
8. Walk T1.26.1 → T1.26.2 → T1.26.3 (the three P1s) sequentially. They touch overlapping files (text.py, upload.py) so don't parallelize.
9. Decide P2 inclusion based on remaining context budget.
10. Re-run `/codex review`. If GATE: PASS, run the verification script + tests, update PR #10, then merge PR #6 + PR #10, run `/canary`, run `/document-release`, hand off.

---

## Final note for the next agent

Phase 1 IS basically done. The bones are right. The lesson Codex taught was about caller-side completeness, not about the architectural design. Don't second-guess the architecture; just close the wiring gaps.

The user is a non-developer founder building a cutting-edge AI-native customer intelligence platform. The architectural standard is high. The Codex findings exist *because* the standard is high — the finding "the queue feature is unreachable from production" is a 9.5/10 review only because the bar was 10/10. A lesser standard would have accepted the silent-drop fallback as "fail-safe."

Your job this session is to translate Codex's findings into clean, minimal fixes; re-verify with the same Codex tool; and ship Phase 1. Then write the Phase 1.5 handoff so the next session can start with the same level of context this one started with.

Hold the bar. Run real Codex. Ship clean.
