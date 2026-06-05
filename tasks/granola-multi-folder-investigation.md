# Granola Multi-Folder Ingest — Feasibility & Incorporation Assessment

**Date:** 2026-06-04
**Status:** INVESTIGATION COMPLETE — data-model decision recommended; v1 UI-scope decision OPEN
**Context:** Phase 3 (Granola frontend) planning. The founder raised that a user may want to
ingest from **multiple Granola folders** (and *add* folders over time), not just pick/change a
single folder. This assesses whether the Granola API allows it and what it costs across the stack.
**Method:** 3 parallel investigation agents (Granola API capability; backend data-model + poll-loop
impact; frontend UX shape). Read-only; evidence cited to file:line / Granola docs.

**Untracked working-tree doc** (not committed) — sibling to `tasks/granola-integration-plan.md` §2.1.

---

## TL;DR

- **Multi-folder is FEASIBLE, additive, and low-risk** — but Granola forces it to be a
  **client-side loop** (one `folder_id` per API call), so "watch N folders" = N calls per cycle.
- **The load-bearing, irreversible decision is the DATA-MODEL SHAPE.** Store a **LIST** of folders
  on the credential, and define the `/connect` input and `/status` response as **arrays**, from day
  one — even if the v1 UI only lets the user pick one folder. Widening a scalar → list later is a
  config migration + adapter rewrite + a breaking frontend-contract change *after* prod credentials
  exist; starting with a list and shipping a 1-element UI is free.
- **A folder filter already includes child/subfolders** (Granola server-side). So "watch the top
  parent folder" transparently covers its whole subtree — single-folder may already satisfy many
  design partners. This is the main argument that multi-folder *UI* can be a fast-follow.
- **OPEN (founder call):** does v1 ship a single-folder picker (list-of-one) with the multi-folder
  loop + multi-select UI as a documented fast-follow, OR build full multi-folder in v1 (the backend
  loop is only ~0.5–1 day)?

---

## 1. Granola API capability (the gate)

| Question | Answer | Evidence |
|---|---|---|
| Multiple folders in one call? | **No.** `list_notes` takes ONE optional `folder_id` (`^fol_[a-zA-Z0-9]{14}$`). No `folder_ids` array, no `workspace_id`. Watch N folders = N calls. | docs.granola.ai/api-reference/list-notes; `api_client.py:244-289` |
| Does a folder filter include subfolders? | **Yes** — "notes in this folder **and any of its child folders**." Folders are nested (`parent_folder_id`). Watching a parent covers its subtree server-side. | docs list-notes / list-folders; `models.py` GranolaFolder.parent_folder_id |
| Can a note be in multiple folders? | **Yes** — `folder_membership` is an array (many-to-many). Overlapping/parent+child selections return the same note twice. | `models.py:106-118,156`; docs get-note |
| "Ingest everything" mode? | **Yes** — omit `folder_id` → all notes in the key's scope. **Latent bug:** adapter sends `folder_id=""` when unset, which likely 400s; must OMIT the param, not send empty. | docs list-notes; `adapter.py:251-253`; `api_client.py:277-280` |
| Rate limits for N folders? | Sustained 5 req/s (300/min) + burst 25/5s. N folders × cursor pages is trivially under budget at MVP scale; pressure is the **initial backfill** (created_after=NULL) detail-fetch fan-out. | docs rate limits; `api_client.py:63,79` |
| Folder list shape | `GET /v1/folders` → `{folders:[{id,name,parent_folder_id}], hasMore, cursor}`, page_size ≤30. Client walks the cursor (cap 20 pages) and returns a **flat** array to the UI. | docs list-folders; `api_client.py:225-242` |

**Note:** the codebase targets the **official public REST API** (`public-api.granola.ai/v1`), not the
reverse-engineered internal `api.granola.ai/v2`. Use the public-API facts above as the contract.

---

## 2. Backend impact (live-transcription-fastapi) — small, additive

- **Folder config has exactly 3 readers + 2 writers, all in this repo:** the poll
  (`adapter.py:252`), the envelope display metadata (`adapter.py:1187`), the `/status` response
  (`granola.py:775-776`); written by `/connect` (`granola.py:569-571`) and the vault reactivate
  UPDATE. **No frontend reader exists yet.**
- **`config` is opaque JSONB** (`schema.prisma:4623`) — adding `config.folders = [{id,name},...]` is a
  **pure additive change, NO migration**.
- **The UNIQUE `(tenant_id,user_id,provider)` forces list-in-config**, not N credential rows — one
  granola credential row per user; N folders MUST be a list inside that row. (`schema.prisma:4640`;
  `user_credentials.py:564-568`.)
- **Poll loop:** change the single `list_notes(folder_id=…)` into a loop over the folder list,
  concatenate summaries, feed the existing per-note loop unchanged.
- **Watermark:** `last_polled_at` is a SINGLE per-credential column and is **correct for a stable
  union** of folders (one `created_after` instant, snapshotted before the first list). It breaks ONLY
  if a folder is **added** mid-life — newly-added folders need a watermark reset. The existing
  `reactivate_credential` path **already nulls `last_polled_at` on folder change** for exactly this
  reason (`user_credentials.py:921-926`) — reuse it for "edit folders."
- **Dedup is already cross-folder-safe:** `external_integration_runs` UNIQUE on
  `(tenant,user,provider,external_id=note_id)` + `process_note` short-circuit means a note in two
  folders ingests once. Add an in-cycle seen-set only as a *cost* optimization (skip the redundant
  detail-fetch). (`adapter.py:389-397,1665`.)
- **Reprocess / signals / 7-day activity are all credential-scoped (no folder predicate)** → unchanged.
- **Estimate:** ~0.5–1 day backend incl. tests + Codex review (the loop + array contracts + legacy
  fallback). Excludes the optional first-poll-async hardening.

**Operational caveat (pre-existing, multiplied by N):** the LOCKED-31 synchronous "save & test" first
poll runs a full backfill inside the HTTP request; Railway's edge caps requests at ~5 min
(`reference_railway_proxy_timeout`). N folders × backfill can approach that. Mitigations (any of):
cap selectable folders (e.g. 5–10); for N>threshold make the first poll fire-and-forget (202+poll) or
a dispatched DBOS workflow (the code already notes this Phase-2.1 mitigation at `granola.py:428-435`);
or synchronously poll only `folders[0]` and let the scheduler backfill the rest.

---

## 3. Frontend impact (eq-frontend) — greenfield; additive if contracts are arrays

- **Zero Granola code today** — fully greenfield on the FE.
- **`MeetingProviderCard` is a single-value state machine** (one status, one identity line) and a pure
  OAuth-redirect — it **cannot** host a key-paste→folder-pick flow or an N-folder list. Granola needs a
  **new `GranolaConnectCard` / `GranolaFolderPicker`** sub-component reusing the `GlassPanel` shell.
- **House-style multi-select primitives already exist** — reuse the `FilterPanel` checkbox-list + chip
  pattern (`components/eq/ui/filter-panel`), Radix `Checkbox`, `Badge` chips, and the cmdk `Command`
  combobox for a searchable picker. No `react-select` dependency exists.
- **Folder list arrives pre-flattened** from `/validate` (server-paginated, cap 20 pages) — no
  client-side paging; a power user beyond 20 pages would silently truncate (note for later, not v1).
- **Edge cases to design:** zero folders selected (disable Connect); account with no folders
  (empty-state); a watched folder deleted in Granola (→ credential `status='error'` → "Needs attention"
  badge + reconnect/edit CTA); 409 "sync running" on reconnect/rotate races.
- **Estimate:** single-folder v1 FE ~1–2 days (new card + folder picker + tRPC/proxy procedures for
  validate/connect/status/disconnect). Rendering the connected state as a **list-of-one** now makes
  going to N folders additive.

---

## 4. Recommended data-model shape (lock NOW, regardless of v1 UI scope)

- **One credential row per user** (one API key). Keep `UNIQUE(tenant,user,provider)`.
- **`config.folders: [{id, name}, ...]`** — the watched-folder LIST lives on the row's JSONB.
  (Optionally a `{mode: 'all' | 'folders', folder_ids: [...]}` discriminator to also capture the
  "ingest everything" mode cleanly.)
- **Backward-compat for one release:** `/connect` writes BOTH `folders` AND legacy singular
  `folder_id`/`folder_name = folders[0]`; the adapter reads `folders` with a `folder_id` fallback.
- **API contracts as arrays from day one:** `/connect` input `folderIds: string[]` (len 1 in v1);
  `/status` returns `folders: [{id,name,status}]` (len 1 in v1). This is the key move that makes the
  frontend contract non-breaking when N-folder lands.
- **Keep the single `last_polled_at` watermark; reset to NULL on any folder-set edit** (reuse the
  reactivate semantics). Per-folder watermarks = a later optimization only if folder churn is frequent.

---

## 5. OPEN decision (founder)

**v1 UI scope:**
- **(A) Single-folder picker in v1 + list-shaped model** — multi-folder backend loop & multi-select UI
  are a documented fast-follow. Leanest v1; relies on "pick your top folder (subfolders included)"
  covering most partners. *(Recommended by all 3 agents.)*
- **(B) Full multi-folder in v1** — build the loop + multi-select now (backend loop ~0.5–1 day;
  FE multi-select modestly more than single-select). Delivers the stated want in one pass, avoids a
  second UI/design cycle.

Either way, **adopt the §4 data-model shape now.**

---

## 6. Build-time probes (verify against the live API before relying on these)

1. Does `folder_id=""` (empty) 400, or behave as "all notes"? (Fix: omit the param when falsy.)
2. Does `GET /v1/notes` accept repeated/comma `folder_id` for a one-call multi-folder fetch? (If yes,
   could replace the N-call loop — unverified; do NOT assume.)
3. Does the `notes[]` **summary** item include `folder_membership`? (Docs show it only on the note
   *detail*; the model has `default_factory=list` which could mask a gap.)
4. `page_size` vs `limit`: the client hardcodes `limit=100` but docs cap `page_size` at 30 — confirm
   the param name + max, and whether 100 silently clamps / 400s.
5. Realistic folder counts for design partners (bounds first-poll latency + the 20-page folder cap).

---

## 7. Backlog linkage

- Supersedes/expands `tasks/granola-integration-plan.md` §2.1 **#13** (`PATCH /folder` / bad-folder
  recovery): the answer is not just "change one folder" but "manage a LIST of folders" (add/remove),
  with edits routed through the reactivate (watermark-reset) path.
- Relates to the LOCKED-31 synchronous-first-poll vs Railway-timeout note (§2 caveat).
