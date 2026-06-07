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

CREATE OR REPLACE FUNCTION vault.list_active_credentials_xt(p_provider text)
RETURNS TABLE (id uuid, tenant_id uuid, user_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, vault, public
AS $fn$
  SELECT id, tenant_id, user_id
  FROM vault.user_credentials
  WHERE provider = p_provider
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

ALTER FUNCTION vault.list_active_credentials_xt(text) OWNER TO neondb_owner;
ALTER FUNCTION vault.list_uninitialized_granola_creds_xt(integer) OWNER TO neondb_owner;

REVOKE ALL ON FUNCTION vault.list_active_credentials_xt(text) FROM PUBLIC;
REVOKE ALL ON FUNCTION vault.list_uninitialized_granola_creds_xt(integer) FROM PUBLIC;

-- Grant EXECUTE to the prod service role only when it exists (prod). In dev the
-- owner connection runs the function directly, so no grant is needed there.
DO $grant$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'eqprod_transcription') THEN
    GRANT EXECUTE ON FUNCTION vault.list_active_credentials_xt(text) TO eqprod_transcription;
    GRANT EXECUTE ON FUNCTION vault.list_uninitialized_granola_creds_xt(integer) TO eqprod_transcription;
  END IF;
END
$grant$;
