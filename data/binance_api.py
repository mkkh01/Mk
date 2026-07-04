"""
CTM Bot - Binance API (multi-URL fallback + live prices + data validation)
"""
import requests
import time
from config import BINANCE_BASE_URL

_BASES = [
    'https://api.binance.com',
    'https://api1.binance.com',
    'https://api2.binance.com',
    'https://api3.binance.com',
    'https://api4.binance.com',
]

_working_base = _BASES[0]


def _try_request(url: str, timeout: int = 10) -> requests.Response:
    global _working_base
    first_error = None
    bases_to_try = [_working_base] + [b for b in _BASES if b != _working_base]
    for base in bases_to_try:
        try:
            u = url.replace('https://api.binance.com', base)
            resp = requests.get(u, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get('code') and data.get('code', 0) < 0:
                    print(f"[BINANCE] {base} error: {data}")
                    continue
                _working_base = base
                return resp
            else:
                print(f"[BINANCE] {base} HTTP {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"[BINANCE] {base} exception: {e}")
            if first_error is None:
                first_error = e
    if first_error:
        raise first_error
    raise Exception(f"All Binance bases failed for {url}")


# ─────────────────────────────────────────────────
#  LIVE PRICE — ticker first, fallback to kline close
# ─────────────────────────────────────────────────

def get_live_price(symbol: str) -> dict:
    """
    Get live price — ticker API first (high priority), fallback to kline.
    Returns {'price': float, 'source': 'ticker'|'kline'|'fallback'}
    """
    # Priority 1: Ticker price (most real-time)
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}"
        resp = _try_request(url, timeout=5)
        data = resp.json()
        if isinstance(data, dict) and 'price' in data:
            return {'price': float(data['price']), 'source': 'ticker'}
    except Exception as e:
        print(f"[LIVE] Ticker failed for {symbol}: {e}")

    # Priority 2: 24hr ticker
    try:
        ticker = get_24hr_ticker(symbol)
        if ticker.get('price', 0) > 0:
            return {'price': ticker['price'], 'source': '24hr_ticker'}
    except Exception:
        pass

    # Priority 3: Last kline close
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
    """Get current price for a symbol (compatibility wrapper)."""
    live = get_live_price(symbol)
    return {'symbol': symbol.upper(), 'price': str(live['price'])}


# ─────────────────────────────────────────────────
#  KLINES + VALIDATION
# ─────────────────────────────────────────────────

def get_klines(symbol: str, interval: str = "1h", limit: int = 100) -> list:
    url = f"https://api.binance.com/api/v3/klines?symbol={symbol.upper()}&interval={interval}&limit={limit}"
    try:
        resp = _try_request(url, timeout=10)
        data = resp.json()
        if isinstance(data, dict) and 'code' in data:
            print(f"[BINANCE klines] Error for {symbol}: {data}")
            return []
        if not isinstance(data, list):
            print(f"[BINANCE klines] Unexpected response: {type(data)}")
            return []
        return _validate_klines(symbol, interval, data)
    except Exception as e:
        print(f"[BINANCE klines] Exception for {symbol}: {e}")
        return []


def _validate_klines(symbol: str, interval: str, data: list) -> list:
    """Validate kline data: check gaps, timestamps, completeness."""
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
            # Check OHLCV values are valid
            try:
                o, h, l, c, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
                if h < l or c < l or c > h or o < l or o > h:
                    gaps.append(f"Row {i}: OHLC inconsistency")
                    continue
                if v < 0:
                    gaps.append(f"Row {i}: negative volume")
                    continue
            except (ValueError, IndexError):
                gaps.append(f"Row {i}: invalid numeric data")
                continue
        elif isinstance(k, dict):
            c = float(k.get('close', k.get('c', 0)))
            if c <= 0:
                gaps.append(f"Row {i}: zero/negative close")
                continue
        validated.append(k)

    # Check timestamp gaps
    if len(validated) >= 2:
        for i in range(1, len(validated)):
            t_prev = _extract_timestamp(validated[i-1])
            t_curr = _extract_timestamp(validated[i])
            if t_prev and t_curr:
                gap = t_curr - t_prev
                expected = interval_ms
                if gap > expected * 1.5:  # allow 50% tolerance
                    gaps.append(f"Time gap: {gap}ms between candle {i} and {i+1} (expected {expected}ms)")

    if gaps:
        print(f"[VALIDATE] {symbol}/{interval}: {len(gaps)} issues — {gaps[:3]}{'...' if len(gaps) > 3 else ''}")

    return validated


def _extract_timestamp(k) -> int | None:
    """Extract open timestamp from kline."""
    if isinstance(k, (list, tuple)) and len(k) > 0:
        try:
            return int(k[0])
        except:
            return None
    elif isinstance(k, dict):
        try:
            return int(k.get('openTime', k.get('t', k.get('timestamp', 0))))
        except:
            return None
    return None


def _interval_to_ms(interval: str) -> int:
    """Convert Binance interval string to milliseconds."""
    unit = interval[-1]
    try:
        value = int(interval[:-1])
    except ValueError:
        return 0
    multipliers = {'m': 60000, 'h': 3600000, 'd': 86400000, 'w': 604800000}
    return value * multipliers.get(unit, 0)


# ─────────────────────────────────────────────────
#  ORDER BOOK + TICKER
# ─────────────────────────────────────────────────

def get_order_book(symbol: str, limit: int = 20) -> dict:
    url = f"https://api.binance.com/api/v3/depth?symbol={symbol.upper()}&limit={limit}"
    try:
        resp = _try_request(url, timeout=10)
        data = resp.json()
        if isinstance(data, dict) and 'code' in data:
            return {'bids': [], 'asks': []}
        return data
    except Exception:
        return {'bids': [], 'asks': []}


def get_24hr_ticker(symbol: str) -> dict:
    url = f"https://api.binance.com/api/v3/ticker/24hr?symbol={symbol.upper()}"
    try:
        resp = _try_request(url, timeout=8)
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


# ─────────────────────────────────────────────────
#  DATA EXTRACTION
# ─────────────────────────────────────────────────

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
