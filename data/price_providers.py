"""
CTM Bot — Multi-Source Price Providers
Fetches from Bybit, KuCoin, CoinGecko, CryptoCompare when Binance is blocked.
"""
import requests
import time

# ── Session with retry ──
_session = requests.Session()
_session.headers.update({'User-Agent': 'CTM-Bot/2.2'})


def _get(url: str, timeout: int = 5) -> dict | list | None:
    """GET with timeout, returns parsed JSON or None."""
    try:
        resp = _session.get(url, timeout=timeout)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════
#  LIVE PRICE PROVIDERS
# ═══════════════════════════════════════════════

def bybit_price(symbol: str) -> float | None:
    """Bybit spot ticker. symbol like 'BTCUSDT'."""
    try:
        data = _get(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol.upper()}")
        if data and data.get('retCode') == 0:
            return float(data['result']['list'][0]['lastPrice'])
    except Exception:
        pass
    return None


def kucoin_price(symbol: str) -> float | None:
    """KuCoin ticker. symbol like 'BTC-USDT'."""
    try:
        sym = symbol.upper().replace('USDT', '-USDT')
        data = _get(f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={sym}")
        if data and data.get('code') == '200000':
            return float(data['data']['price'])
    except Exception:
        pass
    return None


def coingecko_price(symbol: str) -> float | None:
    """CoinGecko simple price. Limited to known coins."""
    # Map common symbols to CoinGecko IDs
    coin_map = {
        'BTCUSDT': 'bitcoin', 'ETHUSDT': 'ethereum', 'BNBUSDT': 'binancecoin',
        'XRPUSDT': 'ripple', 'ADAUSDT': 'cardano', 'SOLUSDT': 'solana',
        'DOGEUSDT': 'dogecoin', 'DOTUSDT': 'polkadot', 'MATICUSDT': 'matic-network',
        'SHIBUSDT': 'shiba-inu', 'LTCUSDT': 'litecoin', 'TRXUSDT': 'tron',
        'AVAXUSDT': 'avalanche-2', 'LINKUSDT': 'chainlink', 'UNIUSDT': 'uniswap',
        'ATOMUSDT': 'cosmos', 'XLMUSDT': 'stellar', 'FILUSDT': 'filecoin',
        'APTUSDT': 'aptos', 'ARBUSDT': 'arbitrum', 'OPUSDT': 'optimism',
        'NEARUSDT': 'near', 'VETUSDT': 'vechain', 'ICPUSDT': 'internet-computer',
        'SUIUSDT': 'sui', 'PEPEUSDT': 'pepe', 'WIFUSDT': 'dogwifcoin',
    }
    coin_id = coin_map.get(symbol.upper())
    if not coin_id:
        return None
    try:
        data = _get(f"https://api.coingecko.com/api/v3/simple/price?ids={coin_id}&vs_currencies=usd")
        if data and coin_id in data:
            return float(data[coin_id]['usd'])
    except Exception:
        pass
    return None


def cryptocompare_price(symbol: str) -> float | None:
    """CryptoCompare price. symbol like 'BTC' (no USDT suffix)."""
    try:
        sym = symbol.upper().replace('USDT', '')
        data = _get(f"https://min-api.cryptocompare.com/data/price?fsym={sym}&tsyms=USDT")
        if data and 'USDT' in data:
            return float(data['USDT'])
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════
#  CANDLE / KLINES PROVIDERS
# ═══════════════════════════════════════════════

def bybit_klines(symbol: str, interval: str = "1h", limit: int = 100) -> list:
    """Bybit klines — returns Binance-compatible format."""
    try:
        # Map Binance interval to Bybit interval
        bybit_interval = interval  # Bybit uses same format: 1, 3, 5, 15, 30, 60, 120, 240, 360, 720, D, W, M
        if interval.endswith('m'):
            bybit_interval = interval[:-1]  # 15m -> 15
        elif interval.endswith('h'):
            bybit_interval = str(int(interval[:-1]) * 60)  # 1h -> 60
        elif interval.endswith('d'):
            bybit_interval = 'D'
        elif interval.endswith('w'):
            bybit_interval = 'W'

        data = _get(
            f"https://api.bybit.com/v5/market/kline?"
            f"category=spot&symbol={symbol.upper()}&interval={bybit_interval}&limit={limit}",
            timeout=8
        )
        if data and data.get('retCode') == 0:
            candles = data['result']['list']
            # Convert to Binance format: [openTime, open, high, low, close, volume, ...]
            # Bybit format: [timestamp_ms, open, high, low, close, volume, turnover]
            result = []
            for c in reversed(candles):  # Bybit returns newest first
                result.append([
                    int(c[0]),        # openTime
                    c[1],             # open
                    c[2],             # high
                    c[3],             # low
                    c[4],             # close
                    c[5],             # volume
                    c[0],             # closeTime (approx)
                    c[6],             # quote volume
                    0,                # trades count
                    c[5],             # taker buy base vol
                    c[6],             # taker buy quote vol
                    "0"               # ignore
                ])
            return result
    except Exception:
        pass
    return []


def kucoin_klines(symbol: str, interval: str = "1h", limit: int = 100) -> list:
    """KuCoin klines — returns Binance-compatible format."""
    try:
        sym = symbol.upper().replace('USDT', '-USDT')
        # Map interval
        kc_interval = interval
        if interval == '1m': kc_interval = '1min'
        elif interval == '3m': kc_interval = '3min'
        elif interval == '5m': kc_interval = '5min'
        elif interval == '15m': kc_interval = '15min'
        elif interval == '30m': kc_interval = '30min'
        elif interval == '1h': kc_interval = '1hour'
        elif interval == '2h': kc_interval = '2hour'
        elif interval == '4h': kc_interval = '4hour'
        elif interval == '8h': kc_interval = '8hour'
        elif interval == '12h': kc_interval = '12hour'
        elif interval == '1d': kc_interval = '1day'
        elif interval == '1w': kc_interval = '1week'

        data = _get(
            f"https://api.kucoin.com/api/v1/market/candles?"
            f"type={kc_interval}&symbol={sym}&limit={limit}",
            timeout=8
        )
        if data and data.get('code') == '200000':
            candles = data['data']
            # KuCoin format: [timestamp_s, open, close, high, low, volume, turnover]
            # Convert to Binance format
            result = []
            for c in candles:
                result.append([
                    int(c[0]) * 1000,  # openTime in ms
                    c[1],               # open
                    c[3],               # high (KuCoin: high is index 3)
                    c[4],               # low (KuCoin: low is index 4)
                    c[2],               # close (KuCoin: close is index 2)
                    c[5],               # volume
                    int(c[0]) * 1000,   # closeTime
                    c[6],               # quote volume
                    0, 0, 0, "0"
                ])
            return result
    except Exception:
        pass
    return []


# ═══════════════════════════════════════════════
#  MULTI-SOURCE FALLBACK
# ═══════════════════════════════════════════════

def get_price_any_source(symbol: str, binance_fn=None) -> dict:
    """
    Get live price from ANY available source.
    Priority: Binance → Bybit → KuCoin → CoinGecko → CryptoCompare
    Returns {'price': float, 'source': str}
    """
    # 1. Try Binance (if function provided)
    if binance_fn:
        try:
            result = binance_fn(symbol)
            if result and result.get('price', 0) > 0:
                return {'price': result['price'], 'source': 'binance_' + result.get('source', 'ticker')}
        except Exception:
            pass

    # 2. Bybit
    price = bybit_price(symbol)
    if price and price > 0:
        return {'price': price, 'source': 'bybit'}

    # 3. KuCoin
    price = kucoin_price(symbol)
    if price and price > 0:
        return {'price': price, 'source': 'kucoin'}

    # 4. CoinGecko
    price = coingecko_price(symbol)
    if price and price > 0:
        return {'price': price, 'source': 'coingecko'}

    # 5. CryptoCompare
    price = cryptocompare_price(symbol)
    if price and price > 0:
        return {'price': price, 'source': 'cryptocompare'}

    return {'price': 0, 'source': 'none'}


def get_klines_any_source(symbol: str, interval: str = "1h", limit: int = 100, binance_fn=None) -> list:
    """
    Get klines from ANY available source.
    Priority: Binance → Bybit → KuCoin
    """
    # 1. Binance
    if binance_fn:
        try:
            data = binance_fn(symbol, interval, limit)
            if data and len(data) >= 20:
                return data
        except Exception:
            pass

    # 2. Bybit
    data = bybit_klines(symbol, interval, limit)
    if data and len(data) >= 20:
        return data

    # 3. KuCoin
    data = kucoin_klines(symbol, interval, limit)
    if data and len(data) >= 20:
        return data

    return []
