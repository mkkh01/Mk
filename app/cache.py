# -*- coding: utf-8 -*-
"""طبقة الكاش: Redis إلزامي؛ لا يوجد تخزين بديل داخل الذاكرة."""
import json
import logging

log = logging.getLogger("cache")


class Cache:
    def __init__(self, redis_url: str = ""):
        self._redis_url = redis_url
        self._redis = None
        self.mode = "redis"

    def _require_redis(self):
        if self._redis is None:
            raise RuntimeError("Redis غير متصل؛ التخزين داخل الذاكرة معطل")
        return self._redis

    async def connect(self):
        if not self._redis_url:
            raise RuntimeError("REDIS_URL غير مضبوط؛ Redis مطلوب ولا يوجد fallback محلي")
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            await self._redis.ping()
            self.mode = "redis"
            log.info("متصل بـ Redis بنجاح")
        except Exception as e:
            self._redis = None
            self.mode = "redis_unavailable"
            raise RuntimeError(f"تعذر الاتصال بـ Redis: {e}") from e

    async def close(self):
        if self._redis:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None

    async def get(self, key: str, default=None):
        redis = self._require_redis()
        try:
            v = await redis.get(key)
            return default if v is None else json.loads(v)
        except Exception as e:
            raise RuntimeError(f"Redis get فشل للمفتاح {key}: {e}") from e

    async def set(self, key: str, value, ttl: int = 0):
        redis = self._require_redis()
        try:
            data = json.dumps(value, ensure_ascii=False, default=str)
            if ttl:
                await redis.set(key, data, ex=ttl)
            else:
                await redis.set(key, data)
        except Exception as e:
            raise RuntimeError(f"Redis set فشل للمفتاح {key}: {e}") from e

    async def delete(self, key: str):
        redis = self._require_redis()
        try:
            await redis.delete(key)
        except Exception as e:
            raise RuntimeError(f"Redis delete فشل للمفتاح {key}: {e}") from e

    async def incr(self, key: str) -> int:
        redis = self._require_redis()
        try:
            return int(await redis.incr(key))
        except Exception as e:
            raise RuntimeError(f"Redis incr فشل للمفتاح {key}: {e}") from e

    async def acquire_lock(self, key: str, ttl: int = 50) -> bool:
        """قفل Redis لمنع تداخل دورتين."""
        redis = self._require_redis()
        try:
            return bool(await redis.set(key, "1", ex=ttl, nx=True))
        except Exception as e:
            raise RuntimeError(f"Redis lock فشل للمفتاح {key}: {e}") from e

    async def release_lock(self, key: str):
        redis = self._require_redis()
        try:
            await redis.delete(key)
        except Exception as e:
            raise RuntimeError(f"Redis unlock فشل للمفتاح {key}: {e}") from e
