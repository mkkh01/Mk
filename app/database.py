# -*- coding: utf-8 -*-
"""طبقة قاعدة البيانات: Supabase إن توفرت، وإلا ملفات JSON محلية (وضع التجربة)."""
import asyncio
import json
import logging
import os
from datetime import datetime, timezone

log = logging.getLogger("database")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        }

    async def connect(self):
        if not (self._url and self._key):
            log.warning("SUPABASE غير مضبوط - العمل بتخزين محلي في مجلد data/")
            return
        try:
            from supabase import create_client
            sb = create_client(self._url, self._key)
            await asyncio.to_thread(lambda: sb.table("bot_state").select("key").limit(1).execute())
            self._sb = sb
            self.mode = "supabase"
            log.info("متصل بـ Supabase بنجاح")
        except Exception as e:
            log.warning("تعذر الاتصال بـ Supabase (%s) - التحويل للتخزين المحلي", e)
            self._sb = None
            self.mode = "local"

    # ---------- أدوات التخزين المحلي ----------

    def _load(self, name: str):
        p = self._files[name]
        if not os.path.exists(p):
            return [] if name != "bot_state" else {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return [] if name != "bot_state" else {}

    def _save(self, name: str, data):
        with open(self._files[name], "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, default=str)

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
                await asyncio.to_thread(lambda: self._sb.table("open_trades").insert(trade).execute())
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
                await asyncio.to_thread(lambda: self._sb.table("closed_trades").insert(trade).execute())
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
