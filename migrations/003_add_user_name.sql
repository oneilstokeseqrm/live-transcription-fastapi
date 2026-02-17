-- Migration 003: Add user_name column for downstream speaker attribution
-- Stores display name from JWT for async upload jobs (background task reads back from DB)
-- Nullable, no index needed (never queried by user_name)

ALTER TABLE upload_jobs ADD COLUMN IF NOT EXISTS user_name TEXT;
