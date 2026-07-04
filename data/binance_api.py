import requests
import time
from config import BINANCE_BASE_URL

def get_current_price(symbol: str) -> dict:
    """Get current ticker price. Returns {'symbol': 'BTCUSDT', 'price': 62638.02}"""
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/price?symbol={symbol.upper()}"
    resp = requests.get(url, timeout=10)
    return resp.json()

def get_klines(symbol: str, interval: str = "1h", limit: int = 100) -> list:
    """Get kline/candlestick data.
    Returns list of [open_time, open, high, low, close, volume, close_time, quote_vol, trades, taker_buy_vol, taker_buy_quote_vol, ignore]
    All values are strings, convert to float as needed."""
    url = f"{BINANCE_BASE_URL}/api/v3/klines?symbol={symbol.upper()}&interval={interval}&limit={limit}"
    resp = requests.get(url, timeout=10)
    return resp.json()

def get_order_book(symbol: str, limit: int = 20) -> dict:
    """Get order book. Returns {'bids': [[price, qty],...], 'asks': [[price, qty],...]}"""
    url = f"{BINANCE_BASE_URL}/api/v3/depth?symbol={symbol.upper()}&limit={limit}"
    resp = requests.get(url, timeout=10)
    return resp.json()

def get_24hr_ticker(symbol: str) -> dict:
    """Get 24hr ticker statistics including price change."""
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/24hr?symbol={symbol.upper()}"
    resp = requests.get(url, timeout=10)
    data = resp.json()
    if 'lastPrice' not in data:
        print(f"[BINANCE] Invalid response for {symbol}: {data}")
        return {'symbol': symbol, 'price': 0, 'change_pct': 0, 'high': 0, 'low': 0, 'volume': 0, 'error': str(data.get('msg', 'Unknown'))}
    return {
        'symbol': data['symbol'],
        'price': float(data['lastPrice']),
        'change_pct': float(data['priceChangePercent']),
        'high': float(data['highPrice']),
        'low': float(data['lowPrice']),
        'volume': float(data['volume'])
    }

def get_all_prices(symbols: list) -> dict:
    """Get current prices for multiple symbols. Returns {symbol: price}"""
    url = f"{BINANCE_BASE_URL}/api/v3/ticker/price"
    resp = requests.get(url, timeout=10)
    all_data = resp.json()
    target_symbols = set(s.upper() for s in symbols)
    return {item['symbol']: float(item['price']) for item in all_data if item['symbol'] in target_symbols}

def extract_ohlcv(klines: list) -> dict:
    """Extract OHLCV arrays from raw kline data. Returns dict with lists of floats."""
    opens, highs, lows, closes, volumes = [], [], [], [], []
    for k in klines:
        opens.append(float(k[1]))
        highs.append(float(k[2]))
        lows.append(float(k[3]))
        closes.append(float(k[4]))
        volumes.append(float(k[5]))
    return {
        'open': opens, 'high': highs, 'low': lows,
        'close': closes, 'volume': volumes,
        'timestamps': [int(k[0]) for k in klines]
    }
