
"""
File: config/settings.py
Responsibility: Concrete runtime configuration -- plain Python values,
reading from environment variables with safe fallbacks.
"""

import os
import sys
from contracts.config import SystemConfig

# ---------------------------------------------------------------------------
# CREDENTIALS
# ---------------------------------------------------------------------------
# Telegram Token
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Supabase / Postgres (Transaction Pooler IPv4)
# For asyncpg.create_pool, the DSN must start with postgresql://
DATABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

# Redis Cloud
REDIS_URL = os.environ.get("REDIS_URL")

# ---------------------------------------------------------------------------
# Validation & Formatting
# ---------------------------------------------------------------------------
def validate_and_format():
    global DATABASE_URL, REDIS_URL
    missing = []
    if not TELEGRAM_TOKEN: missing.append("TELEGRAM_BOT_TOKEN")
    if not DATABASE_URL: missing.append("SUPABASE_URL (Postgres DSN)")
    if not SUPABASE_KEY: missing.append("SUPABASE_KEY")
    if not REDIS_URL: missing.append("REDIS_URL")
    
    if missing:
        print(f"CRITICAL CONFIG ERROR: Missing environment variables: {', '.join(missing)}")
        return False

    # Ensure DATABASE_URL is formatted for asyncpg (must not have +asyncpg prefix in DSN)
    # But it must have sslmode=require for Supabase Pooler
    if DATABASE_URL:
        if "sslmode=" not in DATABASE_URL:
            separator = "&" if "?" in DATABASE_URL else "?"
            DATABASE_URL += f"{separator}sslmode=require"
            
    return True

validate_and_format()

# ---------------------------------------------------------------------------
# System Configuration
# ---------------------------------------------------------------------------
settings = SystemConfig(
    telegram_bot_token=TELEGRAM_TOKEN or "MISSING_TOKEN",
    supabase_url=DATABASE_URL or "postgresql://localhost/missing_db",
    supabase_key=SUPABASE_KEY or "MISSING_KEY",
    redis_url=REDIS_URL or "redis://localhost:6379/0",
    default_timeframes=["15m", "1h", "4h"],
    max_active_coins=15,
    simulation_mode=True,
    telegram_chat_id=TELEGRAM_CHAT_ID or "0",
)
