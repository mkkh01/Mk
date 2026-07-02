import requests
import logging
from config import BINANCE_REST, ALLOWED_TIMEFRAMES
from data_layer.cache import get, set

logger = logging.getLogger("fetch_data")
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "MkTradingBot/1.0"})

def fetch_klines(symbol, interval, limit=200):
    """Fetch candlestick data from Binance public API."""
    cache_key = f"klines:{symbol}:{interval}:{limit}"
    cached = get(cache_key)
    if cached:
        return cached

    try:
        resp = SESSION.get(
            f"{BINANCE_REST}/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
        
        candles = []
        for k in data:
            candles.append({
                "open_time": k[0],
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": k[6],
                "quote_volume": float(k[7]),
                "trades": k[8]
            })
        
        set(cache_key, candles, ttl=30)
        return candles
    except Exception as e:
        logger.error(f"Failed to fetch klines {symbol} {interval}: {e}")
        # Return cached data even if expired, or None
        return cached

def fetch_current_price(symbol):
    """Fetch current price for a symbol."""
    try:
        resp = SESSION.get(
            f"{BINANCE_REST}/ticker/price",
            params={"symbol": symbol},
            timeout=10
        )
        resp.raise_for_status()
        return float(resp.json()["price"])
    except Exception as e:
        logger.error(f"Failed to fetch price {symbol}: {e}")
        return None

def fetch_order_book(symbol, limit=10):
    """Fetch order book (bids/asks) for liquidity analysis."""
    cache_key = f"ob:{symbol}:{limit}"
    cached = get(cache_key)
    if cached:
        return cached

    try:
        resp = SESSION.get(
            f"{BINANCE_REST}/depth",
            params={"symbol": symbol, "limit": limit},
            timeout=10
        )
        resp.raise_for_status()
        data = resp.json()
        
        result = {
            "bids": [{"price": float(b[0]), "qty": float(b[1])} for b in data["bids"]],
            "asks": [{"price": float(a[0]), "qty": float(a[1])} for a in data["asks"]],
            "bid_total": sum(float(b[1]) for b in data["bids"]),
            "ask_total": sum(float(a[1]) for a in data["asks"])
        }
        
        set(cache_key, result, ttl=15)
        return result
    except Exception as e:
        logger.error(f"Failed to fetch order book {symbol}: {e}")
        return cached

def fetch_24h_ticker(symbol):
    """Fetch 24h ticker statistics."""
    try:
        resp = SESSION.get(
            f"{BINANCE_REST}/ticker/24hr",
            params={"symbol": symbol},
            timeout=10
        )
        resp.raise_for_status()
        d = resp.json()
        return {
            "price": float(d["lastPrice"]),
            "change_pct": float(d["priceChangePercent"]),
            "high": float(d["highPrice"]),
            "low": float(d["lowPrice"]),
            "volume": float(d["volume"]),
            "quote_volume": float(d["quoteAssetVolume"]),
            "trades": int(d["count"])
        }
    except Exception as e:
        logger.error(f"Failed to fetch 24h ticker {symbol}: {e}")
        return None

def validate_symbol(symbol):
    """Check if a symbol exists on Binance."""
    try:
        resp = SESSION.get(f"{BINANCE_REST}/exchangeInfo", timeout=15)
        resp.raise_for_status()
        symbols = [s["symbol"] for s in resp.json()["symbols"] if s["status"] == "TRADING"]
        return symbol.upper() in symbols
    except:
        return True  # Assume valid if API fails

def validate_timeframe(tf):
    """Check if timeframe is in allowed list."""
    return tf in ALLOWED_TIMEFRAMES