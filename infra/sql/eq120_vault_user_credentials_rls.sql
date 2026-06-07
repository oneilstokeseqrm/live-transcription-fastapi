-- EQ-120: tenant-isolation RLS backstop for vault.user_credentials.
-- live-transcription OWNS this table; its RLS was deferred from EQ-67/EQ-57 and
-- rides this repoint. Mirrors the EQ-57 gateway vault arming + the eq-frontend
-- tenant_isolation_<table> pattern (eq-frontend/sql/rls-policies.sql + rls-force.sql).
--
-- Coordinated with the GUC-aware service code (services/tenant_scope.py:
-- set_tenant_guc / scoped_acquire / tenant_session) that runs
-- set_config('app.tenant_id', <t>, true) INSIDE each vault transaction.
--
-- ⚠️ ORDER OF OPERATIONS (mandatory — Codex EQ-57 P1): deploy the GUC-aware
-- service revision AND fully drain old instances FIRST, THEN apply this. The
-- service connects as the non-owner role eqprod_transcription (NOBYPASSRLS); with
-- RLS on and no GUC set, reads return 0 rows and writes are rejected — so an
-- un-migrated OLD instance still serving after this DDL would break.
--
-- ⚠️ Apply eq120_cron_enumerate.sql FIRST (the cron's cross-tenant scans need the
-- SECURITY DEFINER functions before this RLS makes their direct scans return 0 rows).
--
-- vault.credential_access_log was already armed in EQ-57 (RLS + tenant_isolation);
-- left untouched here. The service's audit writes to it are scoped by the same
-- set_tenant_guc path.
--
-- PROD-FIRST: apply to the prod core Neon (falling-grass-45918218) only. Dev stays
-- inert under the BYPASSRLS owner (EQ-67 prod-first posture). DRY-RUN on a throwaway
-- branch off the prod project first, then apply to the default branch via the Neon
-- control plane. Prod vault is EMPTY, so blast radius is minimal once the
-- order-of-operations holds. Idempotent (DROP POLICY IF EXISTS + CREATE).

ALTER TABLE vault.user_credentials ENABLE ROW LEVEL SECURITY;
ALTER TABLE vault.user_credentials FORCE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS tenant_isolation_user_credentials ON vault.user_credentials;
CREATE POLICY tenant_isolation_user_credentials
  ON vault.user_credentials
  FOR ALL
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
