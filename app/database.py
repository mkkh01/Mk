# -*- coding: utf-8 -*-
"""طبقة قاعدة البيانات: Supabase إلزامية؛ لا يوجد تخزين محلي بديل."""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger("database")

# الأعمدة الحقيقية للجداول (PostgREST يرفض أي مفتاح زائد، لذا تُصفّى الحمولة)
_OPEN_COLS = {"id", "symbol", "side", "entry_price", "qty", "leverage", "margin",
              "notional", "sl", "tp", "risk_amount", "entry_time", "entry_reasons",
              "snapshot", "current_price", "unrealized_pnl", "updated_at"}
_CLOSED_COLS = {"id", "symbol", "side", "entry_price", "exit_price", "qty", "leverage",
                "margin", "sl", "tp", "risk_amount", "entry_time", "exit_time",
                "entry_reasons", "snapshot", "exit_reason", "pnl", "pnl_pct",
                "r_multiple", "fees", "result", "duration_min"}
_SIGNAL_COLS = {"signal_id", "created_at", "symbol", "direction", "status", "score",
                 "entry", "stop_loss", "take_profit", "rr", "setup_id", "htf", "mtf",
                 "ltf", "ema_state", "swing_low", "swing_high", "swing_id", "fib_zone",
                 "stoch_k", "stoch_d", "stoch_cross", "price_confirmation", "reasons",
                 "score_parts", "snapshot"}
SIGNAL_TABLE = "bot_signals"  # جدول signals القديم يخص نظاماً آخر — لا نمسه


def _pick(row: dict, cols: set) -> dict:
    return {k: v for k, v in row.items() if k in cols}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_database(url: str = "", key: str = "", local_dir: str = "data"):
    """مصنع قواعد البيانات:
    - رابط https:// + مفتاح → Supabase API
    - رابط postgresql:// مرفوض؛ يجب استخدام مشروع Supabase المحدد
    - بدون بيانات → يفشل التشغيل؛ Supabase إلزامية
    """
    if (url or "").startswith("postgres"):
        raise RuntimeError(
            "رابط Postgres المباشر غير مدعوم؛ استخدم SUPABASE_URL بصيغة https:// "
            "مع SUPABASE_KEY لمشروع Supabase الصحيح"
        )
    return Database(url, key, local_dir)


class Database:
    def __init__(self, url: str = "", key: str = "", local_dir: str = "data"):
        self._url = url
        self._key = key
        self._sb = None
        self.mode = "local"
        self._dir = local_dir
        os.makedirs(self._dir, exist_ok=True)
        self._files = {
            "open_trades": os.path.join(self._dir, "open_trades.json"),
            "closed_trades": os.path.join(self._dir, "closed_trades.json"),
            "cycles": os.path.join(self._dir, "cycles.json"),
            "equity_snapshots": os.path.join(self._dir, "equity.json"),
            "bot_state": os.path.join(self._dir, "bot_state.json"),
            "signals": os.path.join(self._dir, "signals.json"),
            "swing_points": os.path.join(self._dir, "swings.json"),
            "system_events": os.path.join(self._dir, "events.json"),
        }

    @property
    def degraded(self) -> bool:
        """True إذا كان التخزين البعيد مضبوطاً لكنه سقط للمحلي (وضع SAFE)."""
        configured = bool(self._url and self._key)
        return configured and self._sb is None

    async def connect(self):
        if not (self._url and self._key):
            raise RuntimeError("SUPABASE_URL و SUPABASE_KEY مطلوبان؛ لا يوجد fallback محلي")
        try:
            from supabase import create_client
            sb = create_client(self._url, self._key)
            await asyncio.to_thread(lambda: sb.table("bot_state").select("key").limit(1).execute())
            self._sb = sb
            self.mode = "supabase"
            log.info("متصل بـ Supabase بنجاح")
        except Exception as e:
            self._sb = None
            self.mode = "supabase_unavailable"
            raise RuntimeError(f"تعذر الاتصال بـ Supabase: {e}") from e

    # ---------- منع التخزين المحلي ----------

    def _load(self, name: str):
        raise RuntimeError(f"قراءة التخزين المحلي غير مسموحة ({name}); استخدم Supabase")

    def _save(self, name: str, data):
        raise RuntimeError(f"الكتابة إلى التخزين المحلي غير مسموحة ({name}); استخدم Supabase")

    # ---------- الصفقات المفتوحة ----------

    async def get_open_trades(self) -> list:
        if self._sb:
            try:
                r = await asyncio.to_thread(
                    lambda: self._sb.table("open_trades").select("*").execute()
                )
                return r.data or []
            except Exception as e:
                log.error("get_open_trades فشل: %s", e)
        return self._load("open_trades")

    async def insert_open_trade(self, trade: dict):
        trade = dict(trade)
        trade.setdefault("updated_at", _now_iso())
        if self._sb:
            try:
                await asyncio.to_thread(lambda: self._sb.table("open_trades").insert(_pick(trade, _OPEN_COLS)).execute())
                return
            except Exception as e:
                log.error("insert_open_trade فشل: %s", e)
        rows = self._load("open_trades")
        rows.append(trade)
        self._save("open_trades", rows)

    async def update_open_trade(self, trade_id: str, fields: dict):
        fields = dict(fields)
        fields["updated_at"] = _now_iso()
        if self._sb:
            try:
                await asyncio.to_thread(
                    lambda: self._sb.table("open_trades").update(fields).eq("id", trade_id).execute()
                )
                return
            except Exception as e:
                log.error("update_open_trade فشل: %s", e)
        rows = self._load("open_trades")
        for r in rows:
            if r.get("id") == trade_id:
                r.update(fields)
        self._save("open_trades", rows)

    async def delete_open_trade(self, trade_id: str):
        if self._sb:
            try:
                await asyncio.to_thread(
                    lambda: self._sb.table("open_trades").delete().eq("id", trade_id).execute()
                )
                return
            except Exception as e:
                log.error("delete_open_trade فشل: %s", e)
        rows = [r for r in self._load("open_trades") if r.get("id") != trade_id]
        self._save("open_trades", rows)

    # ---------- الصفقات المغلقة ----------

    async def insert_closed_trade(self, trade: dict):
        if self._sb:
            try:
                await asyncio.to_thread(lambda: self._sb.table("closed_trades").insert(_pick(trade, _CLOSED_COLS)).execute())
                return
            except Exception as e:
                log.error("insert_closed_trade فشل: %s", e)
        rows = self._load("closed_trades")
        rows.append(trade)
        self._save("closed_trades", rows[-2000:])

    async def list_closed_trades(self, limit: int = 50) -> list:
        if self._sb:
            try:
                r = await asyncio.to_thread(
                    lambda: self._sb.table("closed_trades").select("*")
                    .order("exit_time", desc=True).limit(limit).execute()
                )
                return r.data or []
            except Exception as e:
                log.error("list_closed_trades فشل: %s", e)
        rows = self._load("closed_trades")
        rows.sort(key=lambda r: r.get("exit_time", ""), reverse=True)
        return rows[:limit]

    # ---------- الدورات ----------

    async def insert_cycle(self, cycle: dict):
        if self._sb:
            try:
                row = {
                    "cycle_id": cycle["cycle_id"],
                    "started_at": cycle["started_at"],
                    "finished_at": cycle.get("finished_at"),
                    "duration_sec": cycle.get("duration_sec", 0),
                    "status": cycle.get("status", ""),
                    "summary": cycle,
                }
                await asyncio.to_thread(lambda: self._sb.table("cycles").insert(row).execute())
                return
            except Exception as e:
                log.error("insert_cycle فشل: %s", e)
        rows = self._load("cycles")
        rows.append(cycle)
        self._save("cycles", rows[-500:])

    async def get_last_cycle(self):
        if self._sb:
            try:
                r = await asyncio.to_thread(
                    lambda: self._sb.table("cycles").select("summary")
                    .order("cycle_id", desc=True).limit(1).execute()
                )
                if r.data:
                    return r.data[0].get("summary")
                return None
            except Exception as e:
                log.error("get_last_cycle فشل: %s", e)
        rows = self._load("cycles")
        return rows[-1] if rows else None

    # ---------- المحفظة والحالة ----------

    async def insert_equity(self, snap: dict):
        if self._sb:
            try:
                row = {
                    "ts": snap.get("ts", _now_iso()),
                    "equity": snap.get("equity", 0),
                    "balance": snap.get("balance", 0),
                    "realized_pnl": snap.get("realized_pnl", 0),
                    "open_pnl": snap.get("open_pnl", 0),
                    "open_count": snap.get("open_count", 0),
                }
                await asyncio.to_thread(lambda: self._sb.table("equity_snapshots").insert(row).execute())
                return
            except Exception as e:
                log.error("insert_equity فشل: %s", e)
        rows = self._load("equity_snapshots")
        rows.append(snap)
        self._save("equity_snapshots", rows[-2000:])

    async def get_state(self, key: str, default=None):
        if self._sb:
            try:
                r = await asyncio.to_thread(
                    lambda: self._sb.table("bot_state").select("value").eq("key", key).execute()
                )
                if r.data:
                    return r.data[0].get("value", default)
                return default
            except Exception as e:
                log.error("get_state فشل: %s", e)
        return self._load("bot_state").get(key, default)

    async def set_state(self, key: str, value):
        if self._sb:
            try:
                row = {"key": key, "value": value, "updated_at": _now_iso()}
                await asyncio.to_thread(lambda: self._sb.table("bot_state").upsert(row).execute())
                return
            except Exception as e:
                log.error("set_state فشل: %s", e)
        st = self._load("bot_state")
        st[key] = value
        self._save("bot_state", st)

    # ---------- الإشارات (§43) ----------

    async def insert_signal(self, sig: dict):
        sig = dict(sig)
        sig.setdefault("created_at", _now_iso())
        if self._sb:
            try:
                await asyncio.to_thread(lambda: self._sb.table(SIGNAL_TABLE).insert(_pick(sig, _SIGNAL_COLS)).execute())
                return
            except Exception as e:
                log.error("insert_signal فشل: %s", e)
        rows = self._load("signals")
        rows.append(sig)
        self._save("signals", rows[-2000:])

    async def get_signal(self, signal_id: str):
        if self._sb:
            try:
                r = await asyncio.to_thread(
                    lambda: self._sb.table(SIGNAL_TABLE).select("*").eq("signal_id", signal_id).execute()
                )
                return (r.data or [None])[0]
            except Exception as e:
                log.error("get_signal فشل: %s", e)
        for r in self._load("signals"):
            if r.get("signal_id") == signal_id:
                return r
        return None

    async def list_signals(self, limit: int = 20) -> list:
        if self._sb:
            try:
                r = await asyncio.to_thread(
                    lambda: self._sb.table(SIGNAL_TABLE).select("*")
                    .order("created_at", desc=True).limit(limit).execute()
                )
                return r.data or []
            except Exception as e:
                log.error("list_signals فشل: %s", e)
        rows = self._load("signals")
        rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
        return rows[:limit]

    # ---------- Swings والأحداث ----------

    async def insert_swing(self, row: dict):
        row = dict(row)
        row.setdefault("ts", _now_iso())
        if self._sb:
            try:
                await asyncio.to_thread(lambda: self._sb.table("swing_points").insert(row).execute())
                return
            except Exception as e:
                log.error("insert_swing فشل: %s", e)
        rows = self._load("swing_points")
        rows.append(row)
        self._save("swing_points", rows[-2000:])

    async def insert_event(self, event_type: str, payload: dict):
        row = {"event_type": event_type, "payload": payload or {}, "ts": _now_iso()}
        if self._sb:
            try:
                await asyncio.to_thread(lambda: self._sb.table("system_events").insert(row).execute())
                return
            except Exception as e:
                log.error("insert_event فشل: %s", e)
        rows = self._load("system_events")
        rows.append(row)
        self._save("system_events", rows[-2000:])
