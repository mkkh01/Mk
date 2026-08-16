import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone

symbols = ['ADAUSDT', 'DOTUSDT', 'LINKUSDT', 'NEARUSDT', 'XLMUSDT', 'XRPUSDT']
base = 'https://api.binance.com/api/v3'

def get_json(path, params):
    url = base + path + '?' + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.load(response)

def klines(symbol, interval, limit=80):
    rows = get_json('/klines', {'symbol': symbol, 'interval': interval, 'limit': limit})
    closed = rows[:-1]
    closes = [float(row[4]) for row in closed]
    volumes = [float(row[5]) for row in closed]
    if len(closes) < 30:
        return {}
    def pct(a, b):
        return (a / b - 1.0) * 100 if b else 0.0
    ema_fast = sum(closes[-9:]) / 9
    ema_slow = sum(closes[-21:]) / 21
    return {
        'last': closes[-1],
        'change_5': pct(closes[-1], closes[-6]),
        'change_20': pct(closes[-1], closes[-21]),
        'ema9_vs_21_pct': pct(ema_fast, ema_slow),
        'volume_ratio_20': volumes[-1] / (sum(volumes[-21:-1]) / 20) if sum(volumes[-21:-1]) else 0,
    }

out = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'symbols': {}}
for symbol in symbols:
    try:
        ticker = get_json('/ticker/24hr', {'symbol': symbol})
        out['symbols'][symbol] = {
            'price': float(ticker['lastPrice']),
            'change_24h': float(ticker['priceChangePercent']),
            'quote_volume_24h': float(ticker['quoteVolume']),
            '15m': klines(symbol, '15m'),
            '1h': klines(symbol, '1h'),
            '4h': klines(symbol, '4h'),
        }
    except Exception as exc:
        out['symbols'][symbol] = {'error': f'{type(exc).__name__}: {exc}'}
print(json.dumps(out, indent=2))
