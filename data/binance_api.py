"""
CTM Bot - Binance API (multi-URL fallback + live prices + data validation + API status)
"""
import requests
import time
from datetime import datetime, timezone

# Extended fallback chain — handles Render IP blocking
_BASES = [
    'https://api.binance.com',
    'https://api1.binance.com',
    'https://api2.binance.com',
    'https://api3.binance.com',
    'https://api4.binance.com',
    'https://api-gcp.binance.com',
    'https://fapi.binance.com',
]

_working_base = _BASES[0]

# ── API Status tracking ──
_api_status = {
    'last_success': None,
    'last_failure': None,
    'consecutive_failures': 0,
    'total_requests': 0,
    'total_failures': 0,
    'working_base': _BASES[0],
}


def get_api_status() -> dict:
    """Get Binance API connectivity status."""
    now = datetime.now(timezone.utc)
    s = dict(_api_status)
    if s['last_success']:
        s['seconds_since_success'] = (now - s['last_success']).total_seconds()
    if s['last_failure']:
        s['seconds_since_failure'] = (now - s['last_failure']).total_seconds()
    s['online'] = (s['consecutive_failures'] < 3)
    return s


def _try_request(url: str, timeout: int = 5) -> requests.Response:
    """Try request with fallback between Binance API bases."""
    global _working_base
    first_error = None
    bases_to_try = [_working_base] + [b for b in _BASES if b != _working_base]
    _api_status['total_requests'] += 1

    for base in bases_to_try:
        try:
            u = url.replace('https://api.binance.com', base)
            for pat in ['https://api1.binance.com', 'https://api2.binance.com',
                        'https://api3.binance.com', 'https://api4.binance.com']:
                u = u.replace(pat, base)
            resp = requests.get(u, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get('code') and data.get('code', 0) < 0:
                    continue
                _working_base = base
                _api_status['working_base'] = base
                _api_status['last_success'] = datetime.now(timezone.utc)
                _api_status['consecutive_failures'] = 0
                return resp
            elif resp.status_code in (451, 403):
                continue  # geo-blocked, try next
        except Exception as e:
            if first_error is None:
                first_error = e
            continue

    _api_status['last_failure'] = datetime.now(timezone.utc)
    _api_status['consecutive_failures'] += 1
    _api_status['total_failures'] += 1
    if first_error:
        raise first_error
    raise Exception("All Binance bases failed")


# ── LIVE PRICE ──

def get_live_price(symbol: str) -> dict:
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}"
        resp = _try_request(url, timeout=5)
        data = resp.json()
        if isinstance(data, dict) and 'price' in data:
            return {'price': float(data['price']), 'source': 'ticker'}
    except Exception:
        pass
    try:
        ticker = get_24hr_ticker(symbol)
        if ticker.get('price', 0) > 0:
            return {'price': ticker['price'], 'source': '24hr_ticker'}
    except Exception:
        pass
    try:
        klines = get_klines(symbol, '1m', limit=1)
        if klines and isinstance(klines, list):
            k = klines[0]
            if isinstance(k, (list, tuple)):
                return {'price': float(k[4]), 'source': 'kline'}
            elif isinstance(k, dict):
                return {'price': float(k.get('close', k.get('c', 0))), 'source': 'kline'}
    except Exception:
        pass
    return {'price': 0, 'source': 'fallback'}


def get_current_price(symbol: str) -> dict:
    live = get_live_price(symbol)
    return {'symbol': symbol.upper(), 'price': str(live['price'])}


# ── KLINES + VALIDATION ──

def get_klines(symbol: str, interval: str = "1h", limit: int = 100) -> list:
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol.upper()}&interval={interval}&limit={limit}"
    try:
        resp = _try_request(url, timeout=5)
        data = resp.json()
        if isinstance(data, dict) and 'code' in data:
            print(f"[BINANCE klines] Error for {symbol}: {data}")
            return []
        if not isinstance(data, list):
            return []
        return _validate_klines(symbol, interval, data)
    except Exception as e:
        print(f"[BINANCE klines] Exception for {symbol}: {e}")
        return []


def _validate_klines(symbol: str, interval: str, data: list) -> list:
    if not data or len(data) < 2:
        return data
    interval_ms = _interval_to_ms(interval)
    if interval_ms == 0:
        return data
    validated = []
    gaps = []
    for i, k in enumerate(data):
        if isinstance(k, (list, tuple)):
            if len(k) < 6:
                gaps.append(f"Row {i}: incomplete ({len(k)} fields)")
                continue
            try:
                o, h, l, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
                if h < l or c < l or c > h or o < l or o > h:
                    continue
                if v < 0:
                    continue
            except (ValueError, IndexError):
                continue
        elif isinstance(k, dict):
            c = float(k.get('close', k.get('c', 0)))
            if c <= 0:
                continue
        validated.append(k)
    if len(validated) >= 2:
        for i in range(1, len(validated)):
            t_prev = _extract_timestamp(validated[i-1])
            t_curr = _extract_timestamp(validated[i])
            if t_prev and t_curr:
                gap = t_curr - t_prev
                if gap > interval_ms * 1.5:
                    gaps.append(f"Time gap {gap}ms")
    if gaps:
        print(f"[VALIDATE] {symbol}/{interval}: {len(gaps)} issues")
    return validated


def _extract_timestamp(k) -> int | None:
    if isinstance(k, (list, tuple)) and len(k) > 0:
        try: return int(k[0])
        except: return None
    elif isinstance(k, dict):
        try: return int(k.get('openTime', k.get('t', 0)))
        except: return None
    return None


def _interval_to_ms(interval: str) -> int:
    unit = interval[-1]
    try: value = int(interval[:-1])
    except ValueError: return 0
    multipliers = {'m': 60000, 'h': 3600000, 'd': 86400000, 'w': 604800000}
    return value * multipliers.get(unit, 0)


# ── ORDER BOOK + TICKER ──

def get_order_book(symbol: str, limit: int = 20) -> dict:
    url = f"https://api.binance.com/api/v3/depth?symbol={symbol.upper()}&limit={limit}"
    try:
        resp = _try_request(url, timeout=5)
        data = resp.json()
        if isinstance(data, dict) and 'code' in data:
            return {'bids': [], 'asks': []}
        return data
    except Exception:
        return {'bids': [], 'asks': []}


def get_24hr_ticker(symbol: str) -> dict:
    url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol.upper()}"
    try:
        resp = _try_request(url, timeout=5)
        data = resp.json()
        if 'lastPrice' in data:
            return {
                'symbol': data['symbol'],
                'price': float(data['lastPrice']),
                'volume': float(data.get('volume', 0)),
                'high': float(data.get('highPrice', 0)),
                'low': float(data.get('lowPrice', 0)),
                'change': float(data.get('priceChangePercent', 0)),
            }
    except Exception:
        pass
    return {'symbol': symbol, 'price': 0, 'volume': 0, 'high': 0, 'low': 0, 'change': 0}


# ── DATA EXTRACTION ──

def extract_ohlcv(klines: list) -> dict:
    if not klines:
        return {'open': [], 'high': [], 'low': [], 'close': [], 'volume': []}
    opens, highs, lows, closes, volumes = [], [], [], [], []
    for k in klines:
        if isinstance(k, (list, tuple)):
            opens.append(float(k[1]))
            highs.append(float(k[2]))
            lows.append(float(k[3]))
            closes.append(float(k[4]))
            volumes.append(float(k[5]))
        elif isinstance(k, dict):
            opens.append(float(k.get('open', k.get('o', 0))))
            highs.append(float(k.get('high', k.get('h', 0))))
            lows.append(float(k.get('low', k.get('l', 0))))
            closes.append(float(k.get('close', k.get('c', 0))))
            volumes.append(float(k.get('volume', k.get('v', 0))))
    return {'open': opens, 'high': highs, 'low': lows, 'close': closes, 'volume': volumes}
