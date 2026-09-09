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

-- 7) سجل إشارات البوت (§43) — bot_signals لأن signals يخص نظاماً قديماً
create table if not exists bot_signals (
  signal_id text primary key,
  created_at timestamptz not null default now(),
  symbol text not null,
  direction text not null,             -- LONG أو SHORT
  status text not null default 'APPROVED',  -- APPROVED أو WATCH
  score integer not null default 0,
  entry double precision not null default 0,
  stop_loss double precision not null default 0,
  take_profit double precision not null default 0,
  rr double precision not null default 0,
  setup_id text not null default '',
  htf text not null default '', mtf text not null default '', ltf text not null default '',
  ema_state text not null default '',
  swing_low double precision not null default 0,
  swing_high double precision not null default 0,
  swing_id text not null default '',
  fib_zone text not null default '',
  stoch_k double precision, stoch_d double precision,
  stoch_cross text not null default '',
  price_confirmation text not null default '',
  reasons jsonb not null default '[]',
  score_parts jsonb not null default '{}',
  snapshot jsonb not null default '{}'
);
create index if not exists idx_bot_signals_created on signals(created_at desc);
create index if not exists idx_bot_signals_symbol on signals(symbol);

-- 8) نقاط الـ Swings
create table if not exists swing_points (
  id bigserial primary key,
  ts timestamptz not null default now(),
  symbol text not null,
  timeframe text not null default '',
  type text not null default '',       -- up أو down
  low double precision not null default 0,
  high double precision not null default 0,
  quality double precision not null default 0
);
create index if not exists idx_swings_sym on swing_points(symbol, timeframe);

-- 9) أحداث النظام (أعطال، بدء تشغيل، تحذيرات)
create table if not exists system_events (
  id bigserial primary key,
  ts timestamptz not null default now(),
  event_type text not null default '',
  payload jsonb not null default '{}'
);
