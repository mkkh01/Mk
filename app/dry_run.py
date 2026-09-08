# -*- coding: utf-8 -*-
"""تجربة دورة واحدة محلياً بدون Render/Supabase/Telegram.

الاستخدام من جذر المشروع:
    python -m app.dry_run
"""
import asyncio
import logging
import re

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import config as app_config  # noqa: E402
from . import format as fmt  # noqa: E402
from .cache import Cache  # noqa: E402
from .cycle import run_cycle  # noqa: E402
from .database import Database  # noqa: E402
from .history import warmup_history  # noqa: E402
from .market_data import MarketDataClient  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(message)s")


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s)


class PrintNotifier:
    async def send(self, text: str):
        print("\n" + "=" * 60)
        print("📨 إشعار تلجرام (تجربة):")
        print(_strip_html(text))
        print("=" * 60)


class Ctx:
    def __init__(self):
        self.cfg = app_config.settings
        self.cache = Cache(self.cfg.REDIS_URL)
        self.db = Database(self.cfg.SUPABASE_URL, self.cfg.SUPABASE_KEY)
        self.market = MarketDataClient(self.cfg.DATA_SOURCES)
        self.notifier = PrintNotifier()


async def main():
    ctx = Ctx()
    await ctx.cache.connect()
    await ctx.db.connect()
    print(f"الأزواج: {len(ctx.cfg.SYMBOLS)} | الفريمات: {ctx.cfg.TREND_TF}/{ctx.cfg.ENTRY_TF}")
    print("جلب الشموع السابقة (warm-up)...")
    stat = await warmup_history(ctx)
    print(f"المخزون: {stat['ok']} ناجح / {stat['fail']} فاشل في {stat['seconds']} ث")
    summary = await run_cycle(ctx)
    print("\n" + "#" * 60)
    print("ملخص الدورة (كما سيظهر في التلجرام):")
    print(_strip_html(fmt.format_cycle_summary(summary)))
    print("#" * 60)
    await ctx.market.close()
    await ctx.cache.close()


if __name__ == "__main__":
    asyncio.run(main())
