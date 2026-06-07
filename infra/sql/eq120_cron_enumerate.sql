-- EQ-120: cross-tenant credential enumeration for the Granola poll cron.
--
-- After vault.user_credentials is RLS+FORCE'd (eq120_vault_user_credentials_rls.sql),
-- the cron's two cross-tenant scans would return ZERO rows as the non-owner role
-- eqprod_transcription (NOBYPASSRLS) — silently stopping all Granola polling. The
-- cron MUST enumerate every tenant's active credentials to dispatch per-tenant poll
-- workflows; each downstream poll then RE-SCOPES per credential via set_tenant_guc
-- (services/tenant_scope.py), so tenant isolation is preserved from that point on.
--
-- These SECURITY DEFINER functions run as the owner (neondb_owner, BYPASSRLS) to do
-- JUST those two enumerate reads across tenants. (The third cron scan,
-- list_recoverable_import_runs over public.granola_import_runs, needs NO definer:
-- that table is RLS-OFF.)
--
-- Hardened (Codex plan-consult P2): SQL language, STABLE, explicit OWNER, pinned
-- search_path, REVOKE EXECUTE FROM PUBLIC, GRANT EXECUTE only to the service role.
--
-- ORDER: apply this BEFORE eq120_vault_user_credentials_rls.sql (the scoped code
-- calls these). In PROD it also requires public.granola_import_runs to exist
-- (Phase C step 0). Idempotent (CREATE OR REPLACE + IF EXISTS guards). Same file is
-- safe in dev (the eqprod_transcription GRANT is skipped when the role is absent).

-- Granola-specific by design (Codex review P2): a SECURITY DEFINER cross-tenant
-- enumerator should not accept an arbitrary provider string. The only caller is the
-- Granola cron, so the provider is hardcoded rather than parameterized.
CREATE OR REPLACE FUNCTION vault.list_active_credentials_xt()
RETURNS TABLE (id uuid, tenant_id uuid, user_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, vault, public
AS $fn$
  SELECT id, tenant_id, user_id
  FROM vault.user_credentials
  WHERE provider = 'granola'
    AND status = 'active'
    AND archived_at IS NULL
  ORDER BY id ASC
$fn$;

CREATE OR REPLACE FUNCTION vault.list_uninitialized_granola_creds_xt(p_limit integer)
RETURNS TABLE (id uuid, tenant_id uuid, user_id uuid, import_scope text)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, vault, public
AS $fn$
  SELECT uc.id, uc.tenant_id, uc.user_id, uc.config->>'import_scope' AS import_scope
  FROM vault.user_credentials uc
  WHERE uc.provider = 'granola'
    AND uc.status = 'active'
    AND uc.archived_at IS NULL
    AND uc.last_polled_at IS NULL
    AND uc.config->>'import_scope' IN ('history', 'forward')
    AND (
      uc.config->>'import_scope' = 'forward'
      OR NOT EXISTS (
        SELECT 1 FROM public.granola_import_runs r
        WHERE r.credential_id = uc.id
          AND r.state IN ('queued', 'running', 'complete')
      )
    )
  ORDER BY uc.id ASC
  LIMIT p_limit
$fn$;

-- REVOKE from PUBLIC is always safe (no role dependency).
REVOKE ALL ON FUNCTION vault.list_active_credentials_xt() FROM PUBLIC;
REVOKE ALL ON FUNCTION vault.list_uninitialized_granola_creds_xt(integer) FROM PUBLIC;

-- Owner + grant are role-dependent (Codex review P2): guard each on role existence so
-- this file applies cleanly anywhere. In our envs both roles exist (neondb_owner is the
-- Neon DB owner — required as the SECURITY DEFINER owner so the function bypasses RLS;
-- eqprod_transcription is the prod-only service role). In dev the owner connection runs
-- the functions directly, so the eqprod_transcription grant is simply skipped.
DO $perms$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'neondb_owner') THEN
    ALTER FUNCTION vault.list_active_credentials_xt() OWNER TO neondb_owner;
    ALTER FUNCTION vault.list_uninitialized_granola_creds_xt(integer) OWNER TO neondb_owner;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eqprod_transcription') THEN
    GRANT EXECUTE ON FUNCTION vault.list_active_credentials_xt() TO eqprod_transcription;
    GRANT EXECUTE ON FUNCTION vault.list_uninitialized_granola_creds_xt(integer) TO eqprod_transcription;
  END IF;
END
$perms$;
