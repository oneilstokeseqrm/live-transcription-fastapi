-- Migration 004: Add participants_json column for caller-provided participants
-- Stores JSON-serialized list[ParticipantSpec] from POST /upload/init body so the
-- async worker (_process_upload_job) can deserialize and forward them to enrich().
-- Nullable, no index needed (never queried by participants_json; only read alongside the row).

ALTER TABLE upload_jobs ADD COLUMN IF NOT EXISTS participants_json TEXT;
