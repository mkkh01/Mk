# config.py

# 1. التوكن الخاص بك
TELEGRAM_TOKEN = "8913262863:AAFgwkszxhOLpE4IX874HrOKUb1FZUZwYSo"

# 2. الرابط الجديد من خانة Session pooler (الذي يدعم IPv4)
RAW_DATABASE_URL = "postgresql://postgres.lvvcbqqtjygqlxyhiabm:C,TWTpTrK+7#mp.@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"

# تحويل الرابط ليدعم asyncpg
# ملاحظة: تم إزالة ?ssl=require من الرابط واستخدام SSLContext في database.py لضمان توافق أعلى مع Render و Supabase
DATABASE_URL = RAW_DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)

# 3. بقية الإعدادات
ADMIN_ID = 1503808643
DEFAULT_CAPITAL = 10.0
TRADE_FEE = 0.001
BINANCE_WS_URL = "wss://stream.binance.com:9443"
