# -*- coding: utf-8 -*-
"""قاعدة البيانات عبر اتصال Postgres المباشر (رابط postgresql://...).

يُستخدم تلقائياً عندما تكون SUPABASE_URL بصيغة postgres بدل https.
ينشئ الجداول بنفسه عند أول تشغيل (لا حاجة لتنفيذ SQL يدوياً).
عند تعذر الاتصال يتحول للتخزين المحلي كاحتياط.
"""
import json
import logging
from datetime import datetime

log = logging.getLogger("postgres_db")

DDL = """
create table if not exists open_trades (
  id text primary key,
  symbol text not null,
  side text not null,
  entry_price double precision not null,
  qty double precision not null,
  leverage double precision not null default 1,
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
create table if not exists closed_trades (
  id text primary key,
  symbol text not null,
  side text not null,
  entry_price double precision not null,
  exit_price double precision not null,
  qty double precision not null,
  leverage double precision not null default 1,
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
  result text not null default '',
  duration_min double precision not null default 0
);
create index if not exists idx_closed_trades_exit on closed_trades(exit_time desc);
create index if not exists idx_closed_trades_symbol on closed_trades(symbol);
create table if not exists cycles (
  cycle_id bigint primary key,
  started_at timestamptz not null,
  finished_at timestamptz,
  duration_sec double precision not null default 0,
  status text not null default '',
  summary jsonb not null default '{}'
);
create index if not exists idx_cycles_started on cycles(started_at desc);
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
create table if not exists bot_state (
  key text primary key,
  value jsonb not null default '{}',
  updated_at timestamptz not null default now()
);
create table if not exists bot_signals (
  signal_id text primary key,
  created_at timestamptz not null default now(),
  symbol text not null,
  direction text not null,
  status text not null default 'APPROVED',
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
create table if not exists swing_points (
  id bigserial primary key,
  ts timestamptz not null default now(),
  symbol text not null,
  timeframe text not null default '',
  type text not null default '',
  low double precision not null default 0,
  high double precision not null default 0,
  quality double precision not null default 0
);
create index if not exists idx_swings_sym on swing_points(symbol, timeframe);
create table if not exists system_events (
  id bigserial primary key,
  ts timestamptz not null default now(),
  event_type text not null default '',
  payload jsonb not null default '{}'
);
insert into bot_state (key, value)
values ('realized_pnl', '{"total": 0}')
on conflict (key) do nothing;
"""

_JSON_COLS = {"entry_reasons", "snapshot", "summary", "value", "reasons", "score_parts", "payload"}


def _row(r) -> dict:
    d = dict(r)
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif k in _JSON_COLS and isinstance(v, str):
            try:
                d[k] = json.loads(v)
            except (TypeError, ValueError):
                pass
    return d


def _j(v) -> str:
    return json.dumps(v if v is not None else {}, ensure_ascii=False, default=str)


def _ts(v):
    """ISO string → datetime (يقبله asyncpg)."""
    if v is None or isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(v)
    except (TypeError, ValueError):
        return None


class PostgresDatabase:
    """نفس واجهة Database لكن عبر asyncpg مباشرة."""

    def __init__(self, dsn: str, local_dir: str = "data"):
        self._dsn = dsn
        self._pool = None
        self.mode = "local"

    @property
    def _ok(self) -> bool:
        return self._pool is not None

    @property
    def degraded(self) -> bool:
        """True إذا كان Postgres مضبوطاً لكن الاتصال ساقط (وضع SAFE)."""
        return bool(self._dsn) and self._pool is None

    async def connect(self):
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=1, max_size=3,
                statement_cache_size=0, timeout=20)
            async with self._pool.acquire() as c:
                await c.execute(DDL)
            self.mode = "postgres"
            log.info("متصل بـ Postgres مباشرة + الجداول جاهزة ✅")
        except Exception as e:
            self._pool = None
            self.mode = "supabase_unavailable"
            raise RuntimeError(f"تعذر الاتصال بقاعدة Supabase عبر Postgres: {e}") from e

    async def close(self):
        if self._pool:
            try:
                await self._pool.close()
            except Exception:
                pass
            self._pool = None

    # ---------- الصفقات المفتوحة ----------

    async def get_open_trades(self) -> list:
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            async with self._pool.acquire() as c:
                rows = await c.fetch("select * from open_trades order by entry_time")
                return [_row(r) for r in rows]
        except Exception as e:
            log.error("get_open_trades فشل: %s", str(e)[:150])
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")

    async def insert_open_trade(self, trade: dict):
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            async with self._pool.acquire() as c:
                await c.execute(
                    """insert into open_trades
                       (id,symbol,side,entry_price,qty,leverage,margin,notional,sl,tp,
                        risk_amount,entry_time,entry_reasons,snapshot,current_price,unrealized_pnl)
                       values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::timestamptz,
                               $13::jsonb,$14::jsonb,$15,$16)""",
                    trade["id"], trade["symbol"], trade["side"], float(trade["entry_price"]),
                    float(trade["qty"]), float(trade.get("leverage", 1)), float(trade.get("margin", 0)),
                    float(trade.get("notional", 0)), float(trade["sl"]), float(trade["tp"]),
                    float(trade.get("risk_amount", 0)), _ts(trade["entry_time"]),
                    _j(trade.get("entry_reasons", [])), _j(trade.get("snapshot", {})),
                    float(trade.get("current_price") or trade["entry_price"]),
                    float(trade.get("unrealized_pnl", 0)))
        except Exception as e:
            log.error("insert_open_trade فشل: %s", str(e)[:150])
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")

    async def update_open_trade(self, trade_id: str, fields: dict):
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            sets, vals = [], []
            i = 1
            for k in ("current_price", "unrealized_pnl"):
                if k in fields and fields[k] is not None:
                    sets.append(f"{k} = ${i}")
                    vals.append(float(fields[k]))
                    i += 1
            if not sets:
                return
            sets.append("updated_at = now()")
            async with self._pool.acquire() as c:
                await c.execute(f"update open_trades set {', '.join(sets)} where id = ${i}", *vals, trade_id)
        except Exception as e:
            raise RuntimeError(f"update_open_trade فشل: {e}") from e

    async def delete_open_trade(self, trade_id: str):
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            async with self._pool.acquire() as c:
                await c.execute("delete from open_trades where id = $1", trade_id)
        except Exception as e:
            raise RuntimeError(f"delete_open_trade فشل: {e}") from e

    # ---------- الصفقات المغلقة ----------

    async def insert_closed_trade(self, trade: dict):
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            async with self._pool.acquire() as c:
                await c.execute(
                    """insert into closed_trades
                       (id,symbol,side,entry_price,exit_price,qty,leverage,margin,sl,tp,
                        risk_amount,entry_time,exit_time,entry_reasons,snapshot,exit_reason,
                        pnl,pnl_pct,r_multiple,fees,result,duration_min)
                       values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12::timestamptz,$13::timestamptz,
                               $14::jsonb,$15::jsonb,$16,$17,$18,$19,$20,$21,$22)""",
                    trade["id"], trade["symbol"], trade["side"], float(trade["entry_price"]),
                    float(trade["exit_price"]), float(trade["qty"]), float(trade.get("leverage", 1)),
                    float(trade.get("margin", 0)), float(trade["sl"]), float(trade["tp"]),
                    float(trade.get("risk_amount", 0)), _ts(trade["entry_time"]), _ts(trade["exit_time"]),
                    _j(trade.get("entry_reasons", [])), _j(trade.get("snapshot", {})),
                    trade.get("exit_reason", ""), float(trade.get("pnl", 0)),
                    float(trade.get("pnl_pct", 0)), float(trade.get("r_multiple", 0)),
                    float(trade.get("fees", 0)), trade.get("result", ""),
                    float(trade.get("duration_min", 0)))
        except Exception as e:
            log.error("insert_closed_trade فشل: %s", str(e)[:150])
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")

    async def list_closed_trades(self, limit: int = 50) -> list:
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            async with self._pool.acquire() as c:
                rows = await c.fetch(
                    "select * from closed_trades order by exit_time desc limit $1", int(limit))
                return [_row(r) for r in rows]
        except Exception as e:
            log.error("list_closed_trades فشل: %s", str(e)[:150])
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")

    # ---------- الدورات ----------

    async def insert_cycle(self, cycle: dict):
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            async with self._pool.acquire() as c:
                await c.execute(
                    """insert into cycles (cycle_id,started_at,finished_at,duration_sec,status,summary)
                       values ($1,$2::timestamptz,$3::timestamptz,$4,$5,$6::jsonb)
                       on conflict (cycle_id) do update set finished_at = excluded.finished_at,
                         duration_sec = excluded.duration_sec, status = excluded.status,
                         summary = excluded.summary""",
                    int(cycle["cycle_id"]), _ts(cycle["started_at"]), _ts(cycle.get("finished_at")),
                    float(cycle.get("duration_sec", 0)), cycle.get("status", ""), _j(cycle))
        except Exception as e:
            log.error("insert_cycle فشل: %s", str(e)[:150])
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")

    async def get_last_cycle(self):
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            async with self._pool.acquire() as c:
                r = await c.fetchrow("select summary from cycles order by cycle_id desc limit 1")
                if r:
                    v = r["summary"]
                    return json.loads(v) if isinstance(v, str) else dict(v)
                return None
        except Exception as e:
            log.error("get_last_cycle فشل: %s", str(e)[:150])
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")

    # ---------- المحفظة والحالة ----------

    async def insert_equity(self, snap: dict):
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            async with self._pool.acquire() as c:
                await c.execute(
                    """insert into equity_snapshots (ts,equity,balance,realized_pnl,open_pnl,open_count)
                       values ($1::timestamptz,$2,$3,$4,$5,$6)""",
                    _ts(snap.get("ts")), float(snap.get("equity", 0)), float(snap.get("balance", 0)),
                    float(snap.get("realized_pnl", 0)), float(snap.get("open_pnl", 0)),
                    int(snap.get("open_count", 0)))
        except Exception as e:
            log.error("insert_equity فشل: %s", str(e)[:150])
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")

    async def get_state(self, key: str, default=None):
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            async with self._pool.acquire() as c:
                r = await c.fetchrow("select value from bot_state where key = $1", key)
                if r:
                    v = r["value"]
                    return json.loads(v) if isinstance(v, str) else dict(v)
                return default
        except Exception as e:
            log.error("get_state فشل: %s", str(e)[:150])
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")

    async def set_state(self, key: str, value):
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            async with self._pool.acquire() as c:
                await c.execute(
                    """insert into bot_state (key,value,updated_at) values ($1,$2::jsonb,now())
                       on conflict (key) do update set value = excluded.value, updated_at = now()""",
                    key, _j(value))
        except Exception as e:
            log.error("set_state فشل: %s", str(e)[:150])
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")

    # ---------- الإشارات (§43) ----------

    async def insert_signal(self, sig: dict):
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            async with self._pool.acquire() as c:
                await c.execute(
                    """insert into bot_signals
                       (signal_id,symbol,direction,status,score,entry,stop_loss,take_profit,rr,
                        setup_id,htf,mtf,ltf,ema_state,swing_low,swing_high,swing_id,fib_zone,
                        stoch_k,stoch_d,stoch_cross,price_confirmation,reasons,score_parts,snapshot)
                       values ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,
                               $19,$20,$21,$22,$23::jsonb,$24::jsonb,$25::jsonb)
                       on conflict (signal_id) do nothing""",
                    sig.get("signal_id"), sig.get("symbol"), sig.get("direction"),
                    sig.get("status", "APPROVED"), int(sig.get("score", 0)),
                    float(sig.get("entry", 0)), float(sig.get("stop_loss", 0)),
                    float(sig.get("take_profit", 0)), float(sig.get("rr", 0)),
                    sig.get("setup_id", ""), sig.get("htf", ""), sig.get("mtf", ""),
                    sig.get("ltf", ""), sig.get("ema_state", ""),
                    float(sig.get("swing_low", 0)), float(sig.get("swing_high", 0)),
                    sig.get("swing_id", ""), sig.get("fib_zone", ""),
                    sig.get("stoch_k"), sig.get("stoch_d"),
                    sig.get("stoch_cross", ""), sig.get("price_confirmation", ""),
                    _j(sig.get("reasons", [])), _j(sig.get("score_parts", {})),
                    _j(sig.get("snapshot", {})))
        except Exception as e:
            log.error("insert_signal فشل: %s", str(e)[:150])
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")

    async def get_signal(self, signal_id: str):
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            async with self._pool.acquire() as c:
                r = await c.fetchrow("select * from bot_signals where signal_id = $1", signal_id)
                return _row(r) if r else None
        except Exception as e:
            log.error("get_signal فشل: %s", str(e)[:150])
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")

    async def list_signals(self, limit: int = 20) -> list:
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            async with self._pool.acquire() as c:
                rows = await c.fetch(
                    "select * from bot_signals order by created_at desc limit $1", int(limit))
                return [_row(r) for r in rows]
        except Exception as e:
            log.error("list_signals فشل: %s", str(e)[:150])
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")

    async def insert_swing(self, row: dict):
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            async with self._pool.acquire() as c:
                await c.execute(
                    """insert into swing_points (ts,symbol,timeframe,type,low,high,quality)
                       values (coalesce($1::timestamptz, now()),$2,$3,$4,$5,$6,$7)""",
                    _ts(row.get("ts")), row.get("symbol"), row.get("timeframe", ""),
                    row.get("type", ""), float(row.get("low", 0)),
                    float(row.get("high", 0)), float(row.get("quality", 0)))
        except Exception as e:
            log.error("insert_swing فشل: %s", str(e)[:150])
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")

    async def insert_event(self, event_type: str, payload: dict):
        if not self._ok:
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
        try:
            async with self._pool.acquire() as c:
                await c.execute(
                    "insert into system_events (event_type,payload) values ($1,$2::jsonb)",
                    event_type, _j(payload or {}))
        except Exception as e:
            log.error("insert_event فشل: %s", str(e)[:150])
            raise RuntimeError("قاعدة Supabase غير متاحة؛ لا يوجد fallback محلي")
