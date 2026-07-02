import redis
import json
import logging
from config import REDIS_URL

logger = logging.getLogger("cache")

_redis = None

def get_redis():
    global _redis
    if _redis is None:
        try:
            _redis = redis.from_url(REDIS_URL, decode_responses=True)
            _redis.ping()
            logger.info("Redis connected")
        except Exception as e:
            logger.warning(f"Redis connection failed, using in-memory cache: {e}")
            _redis = False
    return _redis if _redis else None

_inmemory = {}

def get(key):
    """Get cached value. Returns None if not found or expired."""
    r = get_redis()
    if r:
        try:
            val = r.get(key)
            if val:
                return json.loads(val)
        except Exception as e:
            logger.warning(f"Redis get error: {e}")
    # Fallback to in-memory
    entry = _inmemory.get(key)
    if entry:
        import time
        if time.time() - entry["ts"] < entry.get("ttl", 300):
            return entry["data"]
        else:
            del _inmemory[key]
    return None

def set(key, value, ttl=300):
    """Cache a value with TTL in seconds."""
    r = get_redis()
    if r:
        try:
            r.setex(key, ttl, json.dumps(value))
            return
        except Exception as e:
            logger.warning(f"Redis set error: {e}")
    import time
    _inmemory[key] = {"data": value, "ts": time.time(), "ttl": ttl}

def delete(key):
    r = get_redis()
    if r:
        try:
            r.delete(key)
        except:
            pass
    _inmemory.pop(key, None)