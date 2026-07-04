"""
CTM Bot - Price Cache
Stores latest prices from klines (avoids Binance ticker IP ban on Render).
"""
from datetime import datetime

_price_cache: dict = {}  # {symbol: {'price': float, 'updated': datetime}}

def update_price(symbol: str, price: float):
    _price_cache[symbol.upper()] = {'price': price, 'updated': datetime.now()}

def get_price(symbol: str) -> dict | None:
    return _price_cache.get(symbol.upper())

def get_all_cached_prices() -> dict:
    return {k: v['price'] for k, v in _price_cache.items()}
