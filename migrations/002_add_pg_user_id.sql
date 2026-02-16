-- Migration 002: Add pg_user_id column for identity bridge
-- Adds optional Postgres User UUID alongside existing Auth0 user_id
-- Nullable, no index needed (queries filter by tenant_id + status, not user_id)
-- Note: upload_jobs is owned by this service (not in eq-frontend Prisma schema)

ALTER TABLE upload_jobs ADD COLUMN IF NOT EXISTS pg_user_id TEXT;
