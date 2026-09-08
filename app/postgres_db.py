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
insert into bot_state (key, value)
values ('realized_pnl', '{"total": 0}')
on conflict (key) do nothing;
"""

_JSON_COLS = {"entry_reasons", "snapshot", "summary", "value"}


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
        from .database import Database
        self._local = Database("", "", local_dir)

    @property
    def _ok(self) -> bool:
        return self._pool is not None

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
            log.warning("تعذر Postgres المباشر (%s) - التحويل للتخزين المحلي", str(e)[:200])
            self._pool = None
            self.mode = "local"

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
            return await self._local.get_open_trades()
        try:
            async with self._pool.acquire() as c:
                rows = await c.fetch("select * from open_trades order by entry_time")
                return [_row(r) for r in rows]
        except Exception as e:
            log.error("get_open_trades فشل: %s", str(e)[:150])
            return await self._local.get_open_trades()

    async def insert_open_trade(self, trade: dict):
        if not self._ok:
            return await self._local.insert_open_trade(trade)
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
            await self._local.insert_open_trade(trade)

    async def update_open_trade(self, trade_id: str, fields: dict):
        if not self._ok:
            return await self._local.update_open_trade(trade_id, fields)
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
            log.error("update_open_trade فشل: %s", str(e)[:150])

    async def delete_open_trade(self, trade_id: str):
        if not self._ok:
            return await self._local.delete_open_trade(trade_id)
        try:
            async with self._pool.acquire() as c:
                await c.execute("delete from open_trades where id = $1", trade_id)
        except Exception as e:
            log.error("delete_open_trade فشل: %s", str(e)[:150])

    # ---------- الصفقات المغلقة ----------

    async def insert_closed_trade(self, trade: dict):
        if not self._ok:
            return await self._local.insert_closed_trade(trade)
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
            await self._local.insert_closed_trade(trade)

    async def list_closed_trades(self, limit: int = 50) -> list:
        if not self._ok:
            return await self._local.list_closed_trades(limit)
        try:
            async with self._pool.acquire() as c:
                rows = await c.fetch(
                    "select * from closed_trades order by exit_time desc limit $1", int(limit))
                return [_row(r) for r in rows]
        except Exception as e:
            log.error("list_closed_trades فشل: %s", str(e)[:150])
            return await self._local.list_closed_trades(limit)

    # ---------- الدورات ----------

    async def insert_cycle(self, cycle: dict):
        if not self._ok:
            return await self._local.insert_cycle(cycle)
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
            await self._local.insert_cycle(cycle)

    async def get_last_cycle(self):
        if not self._ok:
            return await self._local.get_last_cycle()
        try:
            async with self._pool.acquire() as c:
                r = await c.fetchrow("select summary from cycles order by cycle_id desc limit 1")
                if r:
                    v = r["summary"]
                    return json.loads(v) if isinstance(v, str) else dict(v)
                return None
        except Exception as e:
            log.error("get_last_cycle فشل: %s", str(e)[:150])
            return await self._local.get_last_cycle()

    # ---------- المحفظة والحالة ----------

    async def insert_equity(self, snap: dict):
        if not self._ok:
            return await self._local.insert_equity(snap)
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
            await self._local.insert_equity(snap)

    async def get_state(self, key: str, default=None):
        if not self._ok:
            return await self._local.get_state(key, default)
        try:
            async with self._pool.acquire() as c:
                r = await c.fetchrow("select value from bot_state where key = $1", key)
                if r:
                    v = r["value"]
                    return json.loads(v) if isinstance(v, str) else dict(v)
                return default
        except Exception as e:
            log.error("get_state فشل: %s", str(e)[:150])
            return await self._local.get_state(key, default)

    async def set_state(self, key: str, value):
        if not self._ok:
            return await self._local.set_state(key, value)
        try:
            async with self._pool.acquire() as c:
                await c.execute(
                    """insert into bot_state (key,value,updated_at) values ($1,$2::jsonb,now())
                       on conflict (key) do update set value = excluded.value, updated_at = now()""",
                    key, _j(value))
        except Exception as e:
            log.error("set_state فشل: %s", str(e)[:150])
            await self._local.set_state(key, value)
