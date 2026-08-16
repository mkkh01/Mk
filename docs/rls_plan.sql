-- RLS remediation plan for CT_copytrading_bot
-- APPLIED after verifying the backend role and access model.
-- Supabase audit: RLS disabled on all ten public tables and pg_policies is empty.

-- Backend-only option: enable RLS with no anon/authenticated policies.
-- Server-side service_role/private backend access remains separate.
ALTER TABLE public.candles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.decisions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.simulated_trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ws_checkpoints ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.performance_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.decision_component_signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.coins ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.trade_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.logs ENABLE ROW LEVEL SECURITY;

-- Intentionally no broad policies are included. With RLS enabled, anon and
-- authenticated receive no access until narrowly scoped policies are approved.
-- Do not add USING (true) or WITH CHECK (true).

-- If a browser dashboard needs direct REST reads, design authenticated SELECT
-- policies only after confirming the auth model. Keep all engine/ingestion
-- writes server-side; never grant INSERT/UPDATE/DELETE to anon.

-- Verification after approval:
-- SELECT tablename, rowsecurity FROM pg_tables
-- WHERE schemaname = 'public' ORDER BY tablename LIMIT 100;
-- SELECT schemaname, tablename, policyname, roles, cmd
-- FROM pg_policies WHERE schemaname = 'public'
-- ORDER BY tablename, policyname LIMIT 200;

-- Before applying: confirm the running backend DB role, test a backup/rollback,
-- and verify candle ingestion, decisions, simulated trades, dashboard reads,
-- and Telegram summaries in a maintenance window.

-- References:
-- https://supabase.com/docs/guides/database/postgres/row-level-security
-- https://supabase.com/docs/guides/database/postgres/grant-table-access

-- Status: applied and verified on 2026-08-16.
-- Migrations: enable_rls_private_backend_tables; harden_public_function_search_paths.
