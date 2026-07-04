import requests
import time
from config import BINANCE_BASE_URL

def get_current_price(symbol: str) -> dict:
    """Get current ticker price. Returns {'symbol': 'BTCUSDT', 'price': 62638.02}"""
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/price?symbol={symbol.upper()}"
    resp = requests.get(url, timeout=10)
    return resp.json()

def get_klines(symbol: str, interval: str = "1h", limit: int = 100) -> list:
    """Get kline/candlestick data."""
    url = f"{BINANCE_BASE_URL}/api/v3/klines?symbol={symbol.upper()}&interval={interval}&limit={limit}"
    resp = requests.get(url, timeout=10)
    data = resp.json()
    if isinstance(data, dict) and 'code' in data:
        print(f"[BINANCE klines] Error for {symbol}: {data}")
        return []
    return data

def get_order_book(symbol: str, limit: int = 20) -> dict:
    """Get order book."""
    url = f"{BINANCE_BASE_URL}/api/v3/depth?symbol={symbol.upper()}&limit={limit}"
    resp = requests.get(url, timeout=10)
    data = resp.json()
    if isinstance(data, dict) and 'code' in data:
        print(f"[BINANCE orderbook] Error for {symbol}: {data}")
        return {'bids': [], 'asks': []}
    return data

def get_24hr_ticker(symbol: str) -> dict:
    """Get 24hr ticker. Falls back to simple price, then errors."""
    errors = []
    for base in ['https://api.binance.com', 'https://api1.binance.com', 'https://api2.binance.com']:
        try:
            url = f"{base}/api/v3/ticker/24hr?symbol={symbol.upper()}"
            resp = requests.get(url, timeout=8)
            data = resp.json()
            if 'lastPrice' in data:
                return {
                    'symbol': data['symbol'], 'price': float(data['lastPrice']),
                    'change_pct': float(data.get('priceChangePercent', 0)),
                    'high': float(data.get('highPrice', 0)),
                    'low': float(data.get('lowPrice', 0)), 'volume': float(data.get('volume', 0)),
                    '_ok': True
                }
            errors.append(f"{base}: HTTP={resp.status_code} msg={data.get('msg','?')}")
        except Exception as e:
            errors.append(f"{base}: {e}")
    # Try simple price as last resort
    try:
        url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.upper()}"
        resp = requests.get(url, timeout=8)
        data = resp.json()
        if 'price' in data:
            return {'symbol': symbol.upper(), 'price': float(data['price']),
                    'change_pct': 0, 'high': 0, 'low': 0, 'volume': 0, '_ok': True}
    except Exception as e:
        errors.append(f"price: {e}")
    return {'symbol': symbol.upper(), 'price': 0, 'change_pct': 0,
            'high': 0, 'low': 0, 'volume': 0, '_ok': False, '_errors': ' | '.join(errors[-3:])}

def get_all_prices(symbols: list) -> dict:
    """Get current prices for multiple symbols. Returns {symbol: price}"""
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/price"
    resp = requests.get(url, timeout=10)
    all_data = resp.json()
    target_symbols = set(s.upper() for s in symbols)
    return {item['symbol']: float(item['price']) for item in all_data if item['symbol'] in target_symbols}

def extract_ohlcv(klines: list) -> dict:
    """Extract OHLCV arrays from raw kline data. Returns dict with lists of floats."""
    if not klines or not isinstance(klines, list) or not isinstance(klines[0], list):
        print(f"[BINANCE] Invalid kline data: {str(klines)[:200]}")
        return {'open': [], 'high': [], 'low': [], 'close': [], 'volume': [], 'timestamps': []}
    opens, highs, lows, closes, volumes = [], [], [], [], []
    for k in klines:
        try:
            opens.append(float(k[1]))
            highs.append(float(k[2]))
            lows.append(float(k[3]))
            closes.append(float(k[4]))
            volumes.append(float(k[5]))
        except (IndexError, ValueError, TypeError) as e:
            print(f"[BINANCE] Kline parse error: {e} row={k}")
            continue
    return {
        'open': opens, 'high': highs, 'low': lows,
        'close': closes, 'volume': volumes,
        'timestamps': [int(k[0]) for k in klines[:len(opens)]]
    }
