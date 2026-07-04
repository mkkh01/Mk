"""
CTM Bot - Binance API (multi-URL fallback + WebSocket prices)
"""
import requests
import time
import json
from threading import Thread
from config import BINANCE_BASE_URL

# Use multiple Binance API endpoints as fallback
_BASES = [
    'https://api.binance.com',
    'https://api1.binance.com',
    'https://api2.binance.com',
    'https://api3.binance.com',
    'https://api4.binance.com',
]

# Cache for most recent API base that worked
_working_base = _BASES[0]

# WebSocket price cache (populated by WS thread)
_ws_prices = {}
_ws_running = False


def _try_request(url: str, timeout: int = 10) -> requests.Response:
    """Try request with fallback between Binance API bases."""
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


def get_klines(symbol: str, interval: str = "1h", limit: int = 100) -> list:
    """Get kline/candlestick data with multi-base fallback."""
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
        return data
    except Exception as e:
        print(f"[BINANCE klines] Exception for {symbol}: {e}")
        return []


def get_order_book(symbol: str, limit: int = 20) -> dict:
    """Get order book with fallback."""
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
    """Get 24hr ticker with fallback."""
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


def get_current_price(symbol: str) -> dict:
    """Get current price for a symbol."""
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}"
    try:
        resp = _try_request(url, timeout=5)
        data = resp.json()
        if isinstance(data, dict) and 'price' in data:
            return {'symbol': data['symbol'], 'price': data['price']}
    except Exception:
        pass
    return {'symbol': symbol, 'price': '0'}


def extract_ohlcv(klines: list) -> dict:
    """Extract OHLCV arrays from kline data."""
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
