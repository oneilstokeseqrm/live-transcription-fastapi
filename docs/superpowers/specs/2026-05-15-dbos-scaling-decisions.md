# DBOS Scaling Decisions — Single Replica V1, Multi-Replica-Ready

**Date:** 2026-05-15
**Status:** LOCKED. Decision made by the user during the Phase 1.5 implementation-plan session.
**Audience:** Any future session that needs to (a) understand WHY V1 is one Railway replica, (b) understand WHAT triggers a revisit, (c) ship the orphan-workflow detector when those triggers fire.
**Companions:**
- `docs/superpowers/plans/2026-05-15-async-orchestration-dbos.md` — the Phase 1.5 implementation plan that wires the multi-replica-ready config.
- `docs/superpowers/specs/2026-05-12-contact-quality-initiative-design.md` — Section 7.2 references this spec.

---

## 1. The decision in one sentence

Phase 1.5 ships with **one Railway replica running `uvicorn --workers 1`**, AND the DBOS config reads `executor_id` from `RAILWAY_REPLICA_ID` so the deploy is **multi-replica-ready by configuration**. Scaling out (replica count > 1) is gated on shipping a self-hosted orphan-workflow detector first.

## 2. Why one replica, not many

Three independent reasons converged:

1. **The actual workload doesn't justify multi-replica capacity.** Phase 1.5 target volume is 100-1000 approvals/day. Each workflow does ~30-90s of agent work plus DB writes. DBOS Queue caps concurrent workflow execution at 5 (Plan §6.7). Theoretical sustained throughput: 5 concurrent × ~1 workflow/min completion ≈ 7,200 workflows/day. That's ~7x the upper-bound traffic estimate. The system spends most of the day idle. Adding replicas adds zero capacity to a system that isn't capacity-constrained.

2. **The cutting-edge DBOS deployment pattern is horizontal replicas, not multi-worker Uvicorn.** Per DBOS docs and self-hosted-deployment recommendations: each container = one OS process = one DBOS executor. Multiple containers behind a load balancer gives multi-core scaling AND crash isolation AND clean `executor_id` semantics. Multi-worker Uvicorn within one container creates DBOS workflow recovery contention (two processes, both calling `DBOS.launch()`, both attempting to claim the same in-progress workflows from `dbos.workflow_status`) — exactly the wrong shape.

3. **The orphan-workflow detector is non-trivial to build and shouldn't ship before it's needed.** Self-hosted DBOS does NOT handle the case where a replica dies PERMANENTLY (container replaced; replica count decreased; replica's `RAILWAY_REPLICA_ID` reassigned). Workflows tagged with the dead executor_id sit in PENDING / RUNNING state with no living owner. Building the detector that finds these and reassigns them is ~half-day to one-day of work + tests. DBOS Cloud's Conductor handles this automatically; self-hosted, you write it. We don't pay for it until we need it.

## 3. What "multi-replica-ready" means concretely

The plan's M1 wires this in the DBOS config (`services/dbos_runtime.py`):

```python
_CONFIG = DBOSConfig(
    name="live-transcription-fastapi",
    system_database_url=os.environ["DBOS_SYSTEM_DATABASE_URL"],
    executor_id=os.environ.get("RAILWAY_REPLICA_ID"),  # multi-replica-ready
)
```

In production Railway:
- `RAILWAY_REPLICA_ID` is injected automatically (a stable UUID-shaped ID per replica).
- Each replica's DBOS instance launches with its own `executor_id`.
- Workflows started on replica A are tagged with replica A's `executor_id` and only replica A reclaims them on its own restart.
- If we bump Railway's replica count from 1 → N, each new replica gets its own ID, its own DBOS executor, and the system distributes load by virtue of Uvicorn's listening socket being SO_REUSEPORT-shared. No code change needed.

What this single line of config buys us: the door to horizontal scaling stays open at zero ongoing cost.

## 4. What V1 does NOT include (and why)

**The orphan-workflow detector.** This is the periodic sweep that:

1. Lists all `dbos.workflow_status` rows in PENDING or RUNNING state.
2. Cross-references each row's `executor_id` against the set of currently-living replica IDs (queried from Railway's API, or maintained via heartbeats, or inferred from observed activity).
3. For workflows whose executor is gone permanently (no heartbeat for > N minutes, or Railway confirms the replica is destroyed), reassigns the workflow to a living executor (or `NULL` if DBOS supports unowned recovery — check at execution time).

V1 doesn't include this for two reasons:

1. **V1 has only one replica.** With one executor, no orphan situation can arise — when that replica restarts, the new container takes over the same `RAILWAY_REPLICA_ID` slot (verify at execution time; Railway documents this for single-replica services) and DBOS resumes the workflows correctly.
2. **The detector's value is proportional to the rate of replica death + replica count.** At 1 replica, the rate of orphan-causing events is ~zero (container restarts inherit the replica slot). At 5 replicas, replica deaths during a deploy are routine. The detector earns its keep at N > 1, not at N = 1.

## 5. Phase 2 trigger logic (when to revisit)

Build the orphan-workflow detector BEFORE increasing Railway replica count above 1. The triggers that should push this work forward:

### Trigger A — Sustained workload that saturates one replica

Symptoms:
- DBOS Queue (5 concurrent workflow cap) is consistently at or near its cap during business hours.
- Workflow start-to-completion latency (visible in `dbos.workflow_status.created_at` minus completion timestamp) creeps upward as the queue backlogs.
- HTTP request latency (visible in Railway logs) climbs because the single process is contending between FastAPI request handling and workflow step execution.

Action: increase the DBOS Queue concurrency cap first (it's a constant in code, easy bump). If that's not enough OR the FastAPI side is the bottleneck, then build the orphan-detector and bump replica count.

### Trigger B — Phase 2 introduces scheduled re-enrichment workflows

The Phase 2 design (identity state machine + progressive enrichment) involves periodic sweeps across the whole contact base re-enriching contacts in stale states. Even at modest cadence (e.g., 100 contacts/day re-enriched), this multiplies sustained concurrent workflow load beyond Phase 1.5's "occasional bursty approvals" pattern. Phase 2 design MUST include the orphan-detector as a scope item if it touches replica count.

### Trigger C — Operational deploy patterns that destroy and re-create replicas

If we adopt blue/green deploys or canary deploys that DESTROY old replicas while NEW replicas come up, the old replica's in-progress workflows are orphaned the moment the old container goes away. This is independent of total replica count — even at "replica count = 1" with rolling replacement, you can have an old replica processing workflows while a new replica boots, then the old replica's container dies before its workflows complete.

At V1 with a single replica and Railway's default rolling-restart behavior, this is unlikely to bite (Railway typically waits for the new container to be healthy before tearing down the old; the new container picks up the old replica's `RAILWAY_REPLICA_ID` slot and DBOS sees its own workflows on restart). Confirm at execution time by inspecting Railway's deploy strategy. If Railway's behavior changes or we switch to a different deploy model, revisit.

### Trigger D — User explicitly asks to scale out

The user's preference (per session 2026-05-15) is to scale via horizontal replicas, not multi-worker Uvicorn. If/when they say "let's add replicas," the orphan-detector is in scope before the replica count bumps.

## 6. The orphan-workflow detector — design sketch

For the executing session that ships this in Phase 2 (or earlier if Trigger A/C fires):

**Where it runs:**
A `@DBOS.scheduled` (or DBOS Queue with delayed re-enqueue — `@DBOS.scheduled` is flagged for Python deprecation per current DBOS docs) workflow that runs every N minutes. The workflow itself runs on whichever replica DBOS happens to schedule it on; that's fine because DBOS workflows are themselves durable.

**What it does (high-level):**
1. SELECT from `dbos.workflow_status` WHERE status IN ('PENDING', 'RUNNING').
2. Group by `executor_id`.
3. For each `executor_id`, determine if a replica with that ID is currently alive. Sources:
   - Railway API (best; query the service's current replica list).
   - DBOS executor heartbeat table (our own; write a row per replica per N seconds to a `replica_heartbeats` table, expire rows older than M seconds).
   - Inference from `dbos.workflow_status` recent activity (worst; chicken-and-egg).
4. For each workflow whose `executor_id` is NOT in the living set, reassign:
   - Option 1: NULL the `executor_id`. Hope DBOS picks it up via the unowned-workflow recovery path. (Check DBOS docs — this may or may not be a documented behavior.)
   - Option 2: SET `executor_id` to one of the living executors. The chosen executor's DBOS instance resumes it on its next recovery scan.
   - Option 3: Issue a `DBOS.recover` call directly with the workflow_id.

The exact reassignment mechanism depends on DBOS's documented self-hosted recovery API, which evolves; the executing session checks current docs at implementation time.

**Estimated effort:** 50-150 lines of code + tests. Half-day to one full day of focused work.

**Test plan:**
- Kill a replica mid-workflow; verify the orphan-detector picks the workflow up within N minutes.
- Reduce replica count from 5 → 3; verify the orphaned replicas' workflows resume on living replicas.
- Confirm no double-execution under any race (consumer-side MERGE-on-canonical-IDs is the load-bearing dedup, but the detector shouldn't make it worse).

## 7. What this isn't

This spec is NOT about scaling FastAPI HTTP request handling. That's a separate axis. Current production runs `uvicorn --workers 2`; Phase 1.5 V1 drops to `--workers 1` per Plan §4.3. If FastAPI request throughput becomes a bottleneck independently of the workflow path, two paths exist:

1. Add more Railway replicas (subject to the orphan-detector gate above).
2. Increase per-replica concurrency (e.g., `--workers 2` per replica). This is BLOCKED by the same DBOS recovery contention reason: two Uvicorn workers in one container both call `DBOS.launch()`, both contend on workflow recovery. To unblock this within one container, we'd need a strategy like "only worker 0 runs DBOS, the others are FastAPI-only" — not currently a DBOS-supported pattern.

So the only sane HTTP scale-out path is horizontal replicas, which routes through the orphan-detector gate. This is the cutting-edge pattern AND the simplest one.

## 8. Audit trail

- User raised the `--workers 1 vs --workers 2` question 2026-05-15 after reviewing plan v2.
- I produced the plain-English explanation (HTTP capacity vs DBOS recovery semantics; scale math; cutting-edge pattern; lift estimates).
- User aligned on: single replica V1 + `executor_id` from `RAILWAY_REPLICA_ID` (multi-replica-ready) + defer orphan-detector to Phase 2.
- This spec was written to bake the decision into a doc the executing session and future sessions inherit.
- Plan v3 reflects the decision in §3.5, §4.3, §5.4, §10.6, §11/M0+M1, §13, §15.
- Design doc §7.2 references this spec.

## 9. Pre-conditions for shipping multi-replica (when the trigger fires)

The checklist for the session that crosses from 1 → N replicas:

- [ ] Orphan-workflow detector designed, implemented, tested.
- [ ] DBOS docs re-read at implementation time (DBOS evolves; check the current self-hosted-recovery API).
- [ ] Production E2E extended with a "kill-a-replica-mid-workflow" case that asserts the orphan-detector reclaims the workflow.
- [ ] Replica heartbeat mechanism (if needed) in place.
- [ ] Railway API access confirmed (if used as the source of truth for living replicas).
- [ ] Cross-replica observability confirmed (logs from all replicas land in the same view).
- [ ] Cutover plan documented (bump replica count to 2; confirm orphan-detector runs; confirm workflows distribute correctly; only THEN bump higher).
- [ ] The session updates this spec marking the trigger that fired and the date.

## 10. Updates to this spec

This spec is dated and locked at write time. Future updates should preserve the audit trail:

- If a trigger fires and we ship the orphan-detector, append a §11 "Implementation history" section recording when and why.
- If the decision is revisited and changed (e.g., the user later prefers multi-worker Uvicorn after all), append the revisit + new decision; don't overwrite §1's "decision in one sentence."
- If DBOS's API for executor management changes substantively, append a "DBOS API change history" section.
