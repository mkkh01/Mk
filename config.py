# config.py

# 1. التوكن الخاص بك
TELEGRAM_TOKEN = "PUT_YOUR_NEW_TOKEN_HERE"

# 2. رابط Supabase Session Pooler الصحيح
# ⚠️ لازم يكون port 6543 وليس 5432
RAW_DATABASE_URL = "postgresql://postgres.lvvcbqqtjygqlxyhiabm:C,TWTpTrK+7#mp.@aws-1-eu-central-1.pooler.supabase.com:6543/postgres"

# 3. لا تستخدم postgresql+asyncpg هنا
# asyncpg لا يحتاج هذا prefix، هذا خاص بـ SQLAlchemy URLs في حالات معينة فقط
DATABASE_URL = RAW_DATABASE_URL

# 4. الإعدادات الأخرى
ADMIN_ID = 1503808643
DEFAULT_CAPITAL = 10.0
TRADE_FEE = 0.001
BINANCE_WS_URL = "wss://stream.binance.com:9443"
