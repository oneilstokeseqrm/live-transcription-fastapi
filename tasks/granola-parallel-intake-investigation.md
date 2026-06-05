# Granola Parallel-Intake — Investigation, Recommendation & De-Risked Design (EQ-93)

> **⚠️ STALENESS NOTE (2026-06-05):** the `adapter.py` line references and the "current intake" snapshot
> below PREDATE EQ-91 (B1+B2, merged `de3b1f3`/`922660b`) and will shift AGAIN in B3. On current `main`,
> `run_one_cycle` now gathers notes via a **per-folder loop** (`for target in poll_targets`) with an
> in-cycle seen-set dedup BEFORE the per-note loop, and B2 already added a **partial-skip watermark
> guard** (the shared `last_polled_at` is HELD when any folder is skipped — folds into correction #6's
> resumption reasoning; true per-folder watermarks remain the deferred follow-up). **This is a B4
> fast-follow doc — re-verify all code-location citations against current `main` before building EQ-93.**

**Date:** 2026-06-04
**Status:** INVESTIGATION COMPLETE — recommendation = **fast-follow** (NOT v1). Build-ready design sketch
with the 6 mandatory corrections the adversarial review surfaced.
**Context:** Phase 3 (Granola FE+BE) planning, STEP 4A. The open technical question was: is it worth
parallelizing the SEQUENTIAL Granola intake loop in v1, or is it a fast-follow? Assessed against
Granola's ~5 req/s rate limit, LLM rate/cost limits, downstream queue pressure, the per-note
idempotency anchor, and Railway's ~5-min request cap.
**Method:** multi-agent workflow — 2 codebase ground-truth agents (intake mechanics + constraint
budget) + 3 June-2026 cutting-edge research agents (rate-limited backfill, Python bounded concurrency,
AI-native import UX) + synthesis + **adversarial critique** (which caught 3 verified errors in the
first design sketch). Evidence cited to file:line. Untracked working-tree doc — sibling to
`tasks/granola-multi-folder-investigation.md`.

---

## TL;DR

- **Recommendation: FAST-FOLLOW.** Ship the gated card + **background history-import + progress signal**
  in v1 (Decision #6). That removes the only user-felt pain. Parallel intake is a wall-clock
  optimization on TOP of background import, never a substitute for it.
- **Three grounded reasons it is NOT v1:** (1) the felt pain is the Railway 300s timeout on the
  synchronous first poll — background import fixes that regardless of concurrency; (2) **downstream is
  already parallel** (Lane 1 → EventBridge → SQS → independent consumers) and the **LLM clean is already
  fire-and-forget** in Lane 2 (`asyncio.create_task`), so parallel intake only compresses the intake
  loop's own wall-clock, not end-to-end time-to-value; (3) doing concurrency *safely* requires a real
  idempotency-claim fix + pool resizing that should be done deliberately, not rushed alongside the
  bigger v1 surface.
- **The win when built:** ~5× wall-clock on the backfill (200 notes ≈ 33 min → ≈ 7 min; 500 ≈ 83 → 17).
  Steady-state 5-min polling (a handful of new notes/cycle) does **not** need concurrency at all.
- **⚠️ The naive design is a production HAZARD.** The adversarial review found the first sketch would
  **deadlock/starve the connection pools** and **blow Granola's burst cap** as written. §4 below is the
  corrected, build-ready design. Treat the 6 corrections as mandatory.

---

## 1. Ground truth — the current intake (verified file:line)

- **The sequential loop** is `for note_summary in note_summaries: await process_note(...)` in
  `run_one_cycle` (`adapter.py:269-310`). A second serial pass, `reprocess_pending_notes`
  (`adapter.py:868-1083`), re-handles deferred+failed rows. No `asyncio.gather`, no `Semaphore`, no
  fan-out anywhere.
- **Per note:** 1 idempotency read (`_get_integration_run`, short-circuits SUCCESS/FAILED_PERMANENT) →
  1 Granola `get_note_detail` (`adapter.py:443`) → classify+resolve (1 SQLAlchemy session, per-domain
  `lookup_account_by_domain`) → branch Scenario A (ingest) / C (defer) / D (skip). Scenario A:
  reserve Lane 2 slot → pre-write `status='in_progress'` (the anchor, `adapter.py:659`) → resolve
  contacts → build envelope → `await text_clean_service.process(...)` → `_record_success`.
- **THE REAL BOTTLENECK IS NOT THE LLM.** The expensive GPT-4o structured extraction runs in **Lane 2,
  which is fire-and-forget** (`text_clean_service.py:474-476` `asyncio.create_task`) — it does NOT count
  against the serial loop. What the loop actually blocks on per note: 1 Granola HTTPS fetch + **TWO
  sequential BLOCKING boto3 publishes** (`kinesis.put_record` + `events.put_events`, **no
  `run_in_executor`** → they stall the event loop, `aws_event_publisher.py:168,240`) + ~6–8 Postgres
  round-trips. So per-note wall-clock is hundreds-of-ms-to-low-seconds of mostly-serial network I/O.
- **Idempotency anchor:** composite UNIQUE `(tenant_id, user_id, provider, external_id)` on
  `external_integration_runs`; `process_note` reads the row first and short-circuits; the in_progress
  pre-write before publish is the crash-replay anchor; `ON CONFLICT` COALESCEs `eq_interaction_id` so
  retries never clobber the id. **The hazard under concurrency** (already documented at
  `scheduler.py:249-262`): the read (`adapter.py:389`) and the in_progress write (`:659`) are SEPARATE
  non-atomic statements, and the write is `ON CONFLICT DO UPDATE` — NOT a claim. Two in-flight tasks
  for the same note can both miss the anchor, double-mint `eq_interaction_id`, and double-publish
  (downstream won't dedup — different ids). Today this is unreachable (advisory lock + serial loop).
- **Concurrency guards today:** DBOS `SetWorkflowID` dedup within a 5-min window + a **per-credential
  Postgres advisory lock** (`scheduler.py:294-305`) that serializes whole cycles per credential.
  `GRANOLA_POLL_QUEUE` runs **5 different credentials concurrently** (`scheduler.py:83`).

---

## 2. Constraint budget (the arithmetic)

- **Granola:** sustained **5 req/s** (300/min), burst **25/5s** (`api_client.py:84-88`). Per-note cost =
  **1** `get_note_detail`; listing amortized at ~100/page. A realistic partner (1 user, 200–500
  historical meetings) = **~202–505 Granola calls** for the first full import.
- **LLM (the money/limit constraint):** GPT-4o structured extraction per note (`intelligence_service.py:156-163`,
  `max_retries=2`). K parallel notes = K simultaneous token-heavy extractions → spend scales linearly
  and pushes OpenAI **TPM**. This is why K stays small.
- **Backpressure cap = 50** (`TEXT_CLEAN_MAX_BG_TASKS`, `text_clean_service.py:105`), **SHARED with live
  `/text/clean` traffic**. A background import must NOT consume all 50 or it starves interactive cleans.
- **Railway ~5-min (300s) edge cap** (hard, not configurable): a SEQUENTIAL first import of 200–500 notes
  ≈ 33–83 min; even 5-way ≈ 7–17 min — **both far past 300s**. → The synchronous first poll cannot work
  at partner scale; **background import is structural, independent of concurrency.**
- **Sequential first-import estimate:** ~10s/note effective midpoint → 200 ≈ 33 min, 500 ≈ 83 min.
  Bounded 5-way ≈ 7 / 17 min.

---

## 3. Recommendation — fast-follow

**v1:** gated card + background history-import + "importing N of M" progress signal (Decision #6). This
is the felt win and carries zero new concurrency-correctness risk. **Implement the background import
with the CURRENT serial loop** (correct, just slower).

**Fast-follow (EQ-93):** drop bounded-concurrency fan-out into the background-import path so the wall-clock
falls ~5×, with the idempotency claim + pool sizing baked in (§4). **Steady-state 5-min polling keeps the
serial loop** — it doesn't need concurrency.

This fits the founder's stability-over-bleeding-edge principle: the modern, *simple* parallelism (a
small bounded limiter that also respects Granola's rate limit), deliberately SKIPPING the heavier durable-
queue machinery that would be over-engineering at this scale (§5).

---

## 4. De-risked design sketch — WHEN built (the 6 mandatory corrections)

> The first synthesis proposed a per-cycle `Semaphore(5)` over `process_note`. The adversarial review
> proved that, as written, it would **deadlock the pools** and **blow Granola's burst cap**. These 6
> corrections are the difference between a speed-up and an outage. **All are prerequisites in the EQ-93 PR.**

**Seam:** keep the fan-out **in-process, inside the existing single DBOS step** (`run_one_cycle`), over
the `for note_summary in note_summaries` loop (`adapter.py:269-310`). Do NOT use DBOS per-note step
fan-out (the scheduler already rejected it; SQL-level dedup is load-bearing). For the large-N backfill,
use a **streaming bounded worker** (`asyncio.wait(pending, FIRST_COMPLETED)` refill loop) rather than
`gather` over a 500-item comprehension (avoids task explosion).

1. **POOL SIZING IS A PREREQUISITE (hard blocker).** There are TWO pools:
   - **asyncpg** (`asyncpg_pool.py:105-109`): `max_size=10`, with a documented invariant
     `max_size ≥ 2× GRANOLA_POLL_QUEUE.concurrency` (sized for the serial loop: 1 held lock-conn + 1
     transient per cycle = 2×5 = 10). Fan-out demands `concurrency (held locks) + concurrency×N
     (transient acquires)` = 5 + 25 = **30** → bump `_DEFAULT_MAX_SIZE` to **≥32** and re-derive the
     docstring invariant.
   - **SQLAlchemy engine** (`database.py:97-101`): `pool_size=5 + max_overflow=10` = **15 total**, used
     by `_classify_and_resolve` (one session held per note). 5 cycles × 5 notes = **25 concurrent
     classify sessions** > 15 → blocks/timeouts recorded as spurious transient failures (inflating
     `retry_count` toward FAILED_PERMANENT for pure pool starvation). Raise `pool_size` to cover 25, or
     hard-cap concurrent classify sessions.
   - **Verify Neon's per-database connection ceiling** tolerates asyncpg(≥32) + SQLAlchemy(≥25) + other
     consumers. If you can't raise pools that high, then `concurrency×N + concurrency ≤ max_size` forces
     N=1 (no fan-out) at today's sizes — i.e. **fan-out is infeasible without the pool bump.**

2. **GLOBAL RATE LIMIT, NOT PER-CYCLE.** Replace the per-cycle `Semaphore(5)` with a **process-wide
   token bucket / shared limiter** sized to Granola's 5 req/s. The queue already runs 5 cycles
   concurrently; a per-cycle limiter lets 5×N = up to 25 simultaneous detail fetches on one replica
   (~80 req/s peak) → blows the 25/5s burst cap during multi-user onboarding. The binding global
   constraint is **concurrency(cycles) × N**, not N alone.

3. **ATOMIC IDEMPOTENCY CLAIM (the load-bearing correctness change).** Convert the in_progress pre-write
   into an atomic claim: `INSERT ... ON CONFLICT (tenant_id,user_id,provider,external_id) DO NOTHING
   RETURNING id` at the claim point. Winner owns the note → proceed; loser gets no row → return a new
   `SKIPPED_CONCURRENT` outcome without fetching/publishing. Must be **reconciled with the existing
   `eq_interaction_id` recovery path** (`adapter.py:406-415`): the claim is for CONCURRENT racers; the
   recovery is for SEQUENTIAL retries — distinguish by whether the existing row is from THIS run vs a
   prior cycle. Decide whether Scenario C/D notes get a claim row (needed if import + scheduler can race
   the same credential — see #4). Closes the verified read-then-write gap AND hardens the cross-cycle
   race; the existing COALESCE composes cleanly.

4. **EXCLUDE IMPORT vs 5-MIN SCHEDULER ON THE SAME CREDENTIAL.** The background import MUST take the SAME
   per-credential advisory lock (`_advisory_lock_key`) as `run_cycle_step`, or run THROUGH it. Otherwise
   import + cron fan out the same credential concurrently — the single most likely way to double-publish
   in practice, and it re-lists the same notes during the highest-volume window.

5. **KEEP `_CredentialDeactivated` AS A TRUE CYCLE-ABORT.** Do NOT let `gather(return_exceptions=True)`
   swallow it. Use a shared `asyncio.Event` for the deactivation gates (stop launching NEW tasks; let
   in-flight tasks finish — their internal pre-publish gates abort cheaply), AND inspect gathered
   results for any `_CredentialDeactivated` to set `cycle_aborted`, so end-of-cycle success bookkeeping
   (clear `last_error` / reset `consecutive_failures` / advance `last_polled_at`) is correctly skipped —
   exactly as the serial `break` does today (`adapter.py:296-308,319-354`). The reprocess loop
   (`adapter.py:868-1083`) has its OWN abort + the end-of-cycle liveness re-check (`:340-345`) is coupled
   across BOTH loops — thread the Event into both (defer reprocess fan-out to a 2nd PR, but keep the
   abort/bookkeeping coupling correct).

6. **WATERMARK / RESUMPTION FOR THE LONG IMPORT.** `last_polled_at` advances once at cycle end to
   `cycle_start_at` (`adapter.py:346-348`) — correct for a short cycle, but a crashed 500-note import
   re-runs from scratch (the claim dedups the WRITES but re-pays every `get_note_detail`). If that
   re-fetch cost is unacceptable, add a **coarse per-batch watermark advance for the import path**
   (e.g. after each list page completes), accepting the small gap-risk the `cycle_start_at` snapshot was
   designed to avoid. The import progress signal reads cleanly off the per-note
   `external_integration_runs` writes either way.

**Concurrency width:** **N=5** fan-out (mirrors `GRANOLA_POLL_QUEUE` concurrency=5), but recognize the
binding global Granola constraint is concurrency(cycles)×N — hence the global limiter in #2. Hard ceiling
remains the backpressure cap=50; budget the importer to a 5–10 slice, leaving ≥40 for live traffic.

---

## 5. Cutting-edge choices (June-2026 research)

**ADOPT:** `asyncio.Semaphore` / `anyio.CapacityLimiter` as the bounded primitive; the streaming
`asyncio.wait(FIRST_COMPLETED)` refill loop for the backfill (avoids materializing 500 tasks up front);
the **atomic-claim** (`INSERT…ON CONFLICT DO NOTHING RETURNING`) effectively-once pattern; reuse the
existing Retry-After + jittered backoff (`api_client.py:397-558`, already implemented) and the existing
backpressure cap as the Lane 2 ceiling.

**SKIP as over-engineering at this scale:** DBOS per-note workflow/step fan-out (checkpoint cost; SQL-level
dedup is load-bearing; revisit ONLY if intake moves to >1 replica — then the in-process limiter no longer
bounds global Granola rate and a DBOS/global token bucket becomes necessary); AIMD/adaptive concurrency
(Granola's limit is a known fixed 5 req/s); `asyncio.TaskGroup` (cancel-siblings-on-failure is a footgun
here — one note's failure must not kill the batch); Temporal (no high-throughput cross-service fan-out).

---

## 6. Risks (carry into the EQ-93 PR)

- **asyncpg pool DEADLOCK** (not just slowdown) if #1 not done — documented invariant violated.
- **SQLAlchemy engine starvation** in the classify path → spurious FAILED_PERMANENT from pool timeouts.
- **Granola 429 burst** on one replica from concurrency(cycles)×N if #2 (global limiter) not done.
- **Double-publish** if the atomic claim (#3) or import/scheduler exclusion (#4) is missing.
- **Watermark on a deactivated credential** if `gather` swallows `_CredentialDeactivated` (#5).
- **LLM cost blowout / OpenAI TPM** — cap N=5 + backpressure-share ≤10.
- **Railway 300s is NOT solved by concurrency** — never use it to keep the synchronous first poll alive;
  the background import is the actual fix.
- **Multi-replica latent risk** — the in-process limiter bounds one process; horizontal scale-out
  requires a global token bucket. Flag for any future replica bump.
- **Pre-existing `_persist_intelligence` non-idempotency** (§2.1 #16) — a re-ingesting backfill can
  re-trip it; the import must route through idempotent paths.

---

## 7. Founder framing (plain English)

When a user connects Granola, we pull in their past meetings. Today the first pull happens *inside* the
"connect" click, and Railway kills any web request after 5 minutes — so a user with a few hundred past
meetings sees a connect screen that looks broken even though we're still working. We already decided to
fix that the right way: connect confirms instantly, and the history import runs in the background with an
"importing 12 of 240" progress bar and a done notification. That fix alone removes the pain the user
feels — and everything downstream (the AI that reads each meeting, the graph, the forecasts) already runs
in parallel, so each meeting is still processed at full speed. The remaining question — "should we also
process several meetings at once during that background import?" — is a real speed-up (≈30 min → ≈7 min)
but it's NOT what makes the user happy, and doing it safely needs a careful correctness fix so two
parallel workers can't grab the same meeting twice. So: ship card + background import + progress in v1
(the felt win, lower risk); add the parallel speed-up as a clean fast-follow with the safety fixes baked
in. We use the modern, simple kind of parallelism (a 5-at-a-time limiter that also respects Granola's
rate limit) and deliberately skip the heavier "durable queue" machinery — we can reach for that later
only if we ever run this across multiple servers.

---

## 8. Linkage

- **Linear EQ-93** (parallel-intake investigation) — this doc is its seed/source-of-truth.
- Depends on Decision #6 (background history-import, EQ-92) shipping first — the import is the first
  consumer of the bounded fan-out.
- Relates to §2.1 #16 (`_persist_intelligence` non-idempotency) — the import must route idempotent.
- Full raw investigation (ground truth + 3 research legs + synthesis + critique) ran as workflow
  `wf_78baf845-300`; load-bearing findings persisted here.
