# -*- coding: utf-8 -*-
"""طبقة الكاش: Redis إن توفر، وإلا ذاكرة محلية (وضع التجربة)."""
import json
import logging
import time

log = logging.getLogger("cache")


class Cache:
    def __init__(self, redis_url: str = ""):
        self._redis_url = redis_url
        self._redis = None
        self._mem: dict = {}
        self.mode = "memory"

    async def connect(self):
        if not self._redis_url:
            log.warning("REDIS_URL غير مضبوط - العمل بذاكرة محلية (تُفقد عند إعادة التشغيل)")
            return
        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            await self._redis.ping()
            self.mode = "redis"
            log.info("متصل بـ Redis بنجاح")
        except Exception as e:
            log.warning("تعذر الاتصال بـ Redis (%s) - التحويل للذاكرة المحلية", e)
            self._redis = None
            self.mode = "memory"

    async def close(self):
        if self._redis:
            try:
                await self._redis.close()
            except Exception:
                pass

    # ---------- عمليات أساسية ----------

    async def get(self, key: str, default=None):
        if self._redis:
            try:
                v = await self._redis.get(key)
                return default if v is None else json.loads(v)
            except Exception as e:
                log.warning("Redis get فشل: %s", e)
        item = self._mem.get(key)
        if item is None:
            return default
        val, exp = item
        if exp and time.time() > exp:
            self._mem.pop(key, None)
            return default
        return val

    async def set(self, key: str, value, ttl: int = 0):
        if self._redis:
            try:
                data = json.dumps(value, ensure_ascii=False, default=str)
                if ttl:
                    await self._redis.set(key, data, ex=ttl)
                else:
                    await self._redis.set(key, data)
                return
            except Exception as e:
                log.warning("Redis set فشل: %s", e)
        exp = time.time() + ttl if ttl else 0
        self._mem[key] = (value, exp)

    async def delete(self, key: str):
        if self._redis:
            try:
                await self._redis.delete(key)
            except Exception as e:
                log.warning("Redis delete فشل: %s", e)
        self._mem.pop(key, None)

    async def incr(self, key: str) -> int:
        if self._redis:
            try:
                return int(await self._redis.incr(key))
            except Exception as e:
                log.warning("Redis incr فشل: %s", e)
        cur = self._mem.get(key, (0, 0))[0]
        try:
            cur = int(cur) + 1
        except (TypeError, ValueError):
            cur = 1
        self._mem[key] = (cur, 0)
        return cur

    async def acquire_lock(self, key: str, ttl: int = 50) -> bool:
        """قفل لمنع تداخل دورتين. يرجع True إذا حصل على القفل."""
        if self._redis:
            try:
                return bool(await self._redis.set(key, "1", ex=ttl, nx=True))
            except Exception as e:
                log.warning("Redis lock فشل: %s", e)
        item = self._mem.get(key)
        if item:
            _, exp = item
            if exp and time.time() < exp:
                return False
        self._mem[key] = (True, time.time() + ttl)
        return True

    async def release_lock(self, key: str):
        if self._redis:
            try:
                await self._redis.delete(key)
            except Exception:
                pass
        self._mem.pop(key, None)
