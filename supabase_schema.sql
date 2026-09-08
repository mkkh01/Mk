-- =====================================================
--  مخطط قاعدة بيانات Supabase لنظام التداول الورقي
--  طريقة التركيب: Supabase Dashboard ← SQL Editor ← لصق وتنفيذ
-- =====================================================

-- 1) الصفقات المفتوحة
create table if not exists open_trades (
  id text primary key,
  symbol text not null,
  side text not null,                 -- LONG أو SHORT
  entry_price double precision not null,
  qty double precision not null,
  leverage double precision not null default 5,
  margin double precision not null default 0,
  notional double precision not null default 0,
  sl double precision not null,
  tp double precision not null,
  risk_amount double precision not null default 0,
  entry_time timestamptz not null default now(),
  entry_reasons jsonb not null default '[]',
  snapshot jsonb not null default '{}',
  current_price double precision,
  unrealized_pnl double precision not null default 0,
  updated_at timestamptz not null default now()
);
create index if not exists idx_open_trades_symbol on open_trades(symbol);

-- 2) الصفقات المغلقة
create table if not exists closed_trades (
  id text primary key,
  symbol text not null,
  side text not null,
  entry_price double precision not null,
  exit_price double precision not null,
  qty double precision not null,
  leverage double precision not null default 5,
  margin double precision not null default 0,
  sl double precision not null,
  tp double precision not null,
  risk_amount double precision not null default 0,
  entry_time timestamptz not null,
  exit_time timestamptz not null default now(),
  entry_reasons jsonb not null default '[]',
  snapshot jsonb not null default '{}',
  exit_reason text not null default '',
  pnl double precision not null default 0,
  pnl_pct double precision not null default 0,
  r_multiple double precision not null default 0,
  fees double precision not null default 0,
  result text not null default '',   -- WIN أو LOSS
  duration_min double precision not null default 0
);
create index if not exists idx_closed_trades_exit on closed_trades(exit_time desc);
create index if not exists idx_closed_trades_symbol on closed_trades(symbol);

-- 3) ملخصات الدورات (Summary Cycle)
create table if not exists cycles (
  cycle_id bigint primary key,
  started_at timestamptz not null,
  finished_at timestamptz,
  duration_sec double precision not null default 0,
  status text not null default '',   -- success / partial / failed / skipped
  summary jsonb not null default '{}'
);
create index if not exists idx_cycles_started on cycles(started_at desc);

-- 4) لقطات المحفظة
create table if not exists equity_snapshots (
  id bigserial primary key,
  ts timestamptz not null default now(),
  equity double precision not null default 0,
  balance double precision not null default 0,
  realized_pnl double precision not null default 0,
  open_pnl double precision not null default 0,
  open_count integer not null default 0
);
create index if not exists idx_equity_ts on equity_snapshots(ts desc);

-- 5) حالة البوت (أرقام تراكمية)
create table if not exists bot_state (
  key text primary key,
  value jsonb not null default '{}',
  updated_at timestamptz not null default now()
);

-- قيمة ابتدائية للربح التراكمي
insert into bot_state (key, value)
values ('realized_pnl', '{"total": 0}')
on conflict (key) do nothing;
