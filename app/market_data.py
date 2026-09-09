# -*- coding: utf-8 -*-
"""عميل بيانات السوق متعدد المصادر مع تبديل تلقائي.

المصادر (كلها مجانية وبدون مفاتيح):
  1) Binance سبوت عبر Vision (أساسي - نفس أسعار التطبيق، غير محظور)
  2) OKX (احتياطي)
  3) Gate.io (احتياطي ثانٍ)
  4) KuCoin (احتياطي ثالث)

الرمز الداخلي الموحد بصيغة BTCUSDT ويُترجم حسب المنصة.
"""
import asyncio
import logging

import httpx
import pandas as pd

log = logging.getLogger("market")

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120 Safari/537.36"}


def _base(symbol: str) -> str:
    s = symbol.upper()
    for q in ("USDT", "USDC", "USD"):
        if s.endswith(q):
            return s[: -len(q)]
    return s


def _to_okx(symbol: str) -> str:
    return f"{_base(symbol)}-USDT-SWAP"


def _to_gate(symbol: str) -> str:
    return f"{_base(symbol)}_USDT"


def _to_kucoin(symbol: str) -> str:
    return f"{_base(symbol)}-USDT"


_OKX_BAR = {"5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H"}
_GATE_INV = {"5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h"}
_KUCOIN_TYPE = {"5m": "5min", "15m": "15min", "1h": "1hour", "4h": "4hour"}


def _frame(rows: list) -> pd.DataFrame:
    """rows: قائمة [ts ثواني, o, h, l, c, v] → DataFrame مرتب تصاعدياً."""
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df.drop_duplicates(subset="ts").sort_values("ts").reset_index(drop=True)
    return df


class MarketDataClient:
    def __init__(self, sources: list, timeout: int = 20):
        self.sources = [s.strip().lower() for s in sources if s.strip()]
        self._client: httpx.AsyncClient | None = None
        self._timeout = timeout
        self.last_errors: list = []

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(headers=UA, timeout=self._timeout)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    # ---------------- جلب الشموع ----------------

    async def _okx_klines(self, symbol: str, tf: str, limit: int) -> list:
        c = await self._http()
        r = await c.get("https://www.okx.com/api/v5/market/candles", params={
            "instId": _to_okx(symbol), "bar": _OKX_BAR[tf], "limit": min(limit, 300)})
        r.raise_for_status()
        d = r.json()
        if d.get("code") != "0":
            raise RuntimeError(f"OKX: {d.get('msg')}")
        rows = []
        for k in d.get("data", []):
            rows.append([int(int(k[0]) / 1000), k[1], k[2], k[3], k[4], k[5]])
        return rows

    async def _gate_klines(self, symbol: str, tf: str, limit: int) -> list:
        c = await self._http()
        r = await c.get("https://api.gateio.ws/api/v4/futures/usdt/candlesticks", params={
            "contract": _to_gate(symbol), "interval": _GATE_INV[tf], "limit": min(limit, 2000)})
        r.raise_for_status()
        d = r.json()
        if isinstance(d, dict):
            raise RuntimeError(f"Gate: {d}")
        return [[int(k["t"]), k["o"], k["h"], k["l"], k["c"], k.get("v", 0)] for k in d]

    async def _kucoin_klines(self, symbol: str, tf: str, limit: int) -> list:
        c = await self._http()
        r = await c.get("https://api.kucoin.com/api/v1/market/candles", params={
            "symbol": _to_kucoin(symbol), "type": _KUCOIN_TYPE[tf]})
        r.raise_for_status()
        d = r.json()
        if d.get("code") != "200000":
            raise RuntimeError(f"KuCoin: {d.get('msg')}")
        # [time, open, close, high, low, volume, turnover]
        return [[int(k[0]), k[1], k[3], k[4], k[2], k[5]] for k in d.get("data", [])[:limit]]

    async def _binance_klines(self, symbol: str, tf: str, limit: int) -> list:
        c = await self._http()
        last_err: Exception | None = None
        for base in ("https://data-api.binance.vision", "https://api.binance.com"):
            try:
                r = await c.get(f"{base}/api/v3/klines", params={
                    "symbol": symbol.upper(), "interval": tf, "limit": min(limit, 1000)})
                r.raise_for_status()
                d = r.json()
                if isinstance(d, dict):
                    raise RuntimeError(str(d.get("msg", d)))
                return [[int(k[0] / 1000), k[1], k[2], k[3], k[4], k[5]] for k in d]
            except Exception as e:
                last_err = e
        raise last_err or RuntimeError("Binance failed")

    _FETCH = {
        "okx": _okx_klines,
        "gate": _gate_klines,
        "kucoin": _kucoin_klines,
        "binance": _binance_klines,
    }

    async def fetch_klines(self, symbol: str, timeframe: str, limit: int = 260,
                           min_rows: int = 205) -> tuple[pd.DataFrame | None, str, str]:
        """يرجع (DataFrame أو None, المصدر المستخدم, رسالة الخطأ)."""
        best = None
        best_src = ""
        errors = []
        for src in self.sources:
            fn = self._FETCH.get(src)
            if not fn:
                continue
            try:
                rows = await fn(self, symbol, timeframe, limit)
                df = _frame(rows)
                if len(df) >= min_rows:
                    return df, src, ""
                if best is None or len(df) > len(best):
                    best, best_src = df, src
                errors.append(f"{src}: شموع قليلة ({len(df)})")
            except Exception as e:
                errors.append(f"{src}: {str(e)[:120]}")
        if best is not None and len(best) >= 60:
            return best, best_src, f"بيانات جزئية ({len(best)} شمعة)"
        return None, "", " | ".join(errors) if errors else "لا يوجد مصدر"

    # ---------------- الأسعار الحية (طلب واحد لكل السوق) ----------------

    async def fetch_all_prices(self, symbols: list | None = None) -> tuple[dict, str, str]:
        """يرجع ({BTCUSDT: {price, change_pct}}, المصدر, خطأ)."""
        errors = []
        for src in self.sources:
            try:
                if src == "binance":
                    return await self._binance_tickers(symbols or []), "binance", ""
                if src == "okx":
                    return await self._okx_tickers(), src, ""
                if src == "gate":
                    return await self._gate_tickers(), src, ""
                if src == "kucoin":
                    return await self._kucoin_tickers(), src, ""
            except Exception as e:
                errors.append(f"{src}: {str(e)[:120]}")
        return {}, "", " | ".join(errors) if errors else "لا يوجد مصدر"

    async def _binance_tickers(self, symbols: list) -> dict:
        import json as _json
        c = await self._http()
        last_err: Exception | None = None
        params = {"symbols": _json.dumps([s.upper() for s in symbols], separators=(",", ":"))} if symbols else {}
        for base in ("https://data-api.binance.vision", "https://api.binance.com"):
            try:
                r = await c.get(f"{base}/api/v3/ticker/24hr", params=params or None)
                r.raise_for_status()
                d = r.json()
                if isinstance(d, dict):
                    raise RuntimeError(str(d.get("msg", d))[:100])
                out = {}
                for t in d:
                    try:
                        out[t["symbol"]] = {
                            "price": float(t["lastPrice"]),
                            "change_pct": round(float(t.get("priceChangePercent") or 0), 2),
                        }
                    except (TypeError, ValueError, KeyError):
                        continue
                if not out:
                    raise RuntimeError("empty tickers")
                return out
            except Exception as e:
                last_err = e
        raise last_err or RuntimeError("Binance tickers failed")

    async def _okx_tickers(self) -> dict:
        c = await self._http()
        r = await c.get("https://www.okx.com/api/v5/market/tickers", params={"instType": "SWAP"})
        r.raise_for_status()
        d = r.json()
        if d.get("code") != "0":
            raise RuntimeError(d.get("msg"))
        out = {}
        for t in d.get("data", []):
            inst = t.get("instId", "")
            if not inst.endswith("-USDT-SWAP"):
                continue
            try:
                last = float(t["last"])
                op = float(t.get("open24h") or 0)
                ch = ((last - op) / op * 100) if op else 0.0
                out[inst.replace("-USDT-SWAP", "") + "USDT"] = {"price": last, "change_pct": round(ch, 2)}
            except (TypeError, ValueError):
                continue
        return out

    async def _gate_tickers(self) -> dict:
        c = await self._http()
        r = await c.get("https://api.gateio.ws/api/v4/futures/usdt/tickers")
        r.raise_for_status()
        d = r.json()
        if isinstance(d, dict):
            raise RuntimeError(str(d))
        out = {}
        for t in d:
            contract = t.get("contract", "")
            if not contract.endswith("_USDT"):
                continue
            try:
                out[contract.replace("_USDT", "") + "USDT"] = {
                    "price": float(t["last"]),
                    "change_pct": round(float(t.get("change_percentage") or 0), 2),
                }
            except (TypeError, ValueError):
                continue
        return out

    async def _kucoin_tickers(self) -> dict:
        c = await self._http()
        r = await c.get("https://api.kucoin.com/api/v1/market/allTickers")
        r.raise_for_status()
        d = r.json()
        if d.get("code") != "200000":
            raise RuntimeError(d.get("msg"))
        out = {}
        for t in (d.get("data", {}) or {}).get("ticker", []):
            sym = t.get("symbol", "")
            if not sym.endswith("-USDT"):
                continue
            try:
                out[sym.replace("-USDT", "") + "USDT"] = {
                    "price": float(t["last"]),
                    "change_pct": round(float(t.get("changeRate") or 0) * 100, 2),
                }
            except (TypeError, ValueError):
                continue
        return out
