# config.py

# 1. التوكن الخاص بك
TELEGRAM_TOKEN = "8913262863:AAFgwkszxhOLpE4IX874HrOKUb1FZUZwYSo"

# 2. الرابط الجديد من خانة Transaction pooler (الذي يدعم IPv4)
RAW_DATABASE_URL = "postgresql://postgres:C,TWTpTrK+7#mp.@db.lvvcbqqtjygqlxyhiabm.supabase.co:5432/postgres"

# تحويل الرابط ليدعم asyncpg مع فرض SSL
DATABASE_URL = RAW_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1) + "?ssl=require"

# 3. بقية الإعدادات
ADMIN_ID = 1503808643
DEFAULT_CAPITAL = 10.0
TRADE_FEE = 0.001
BINANCE_WS_URL = "wss://stream.binance.com:9443"
