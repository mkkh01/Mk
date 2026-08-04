"""
File: config/settings.example.py
1. Single Responsibility: Template for ``config/settings.py`` with placeholder values.
2. Consumes: nothing.
3. Produces: A ``SystemConfig`` instance named ``settings``.
4. Downstream: app/main.py, every module that needs credentials.
5. New Dependencies: contracts.config.SystemConfig.
6. Touches Section 6 bugs? No.
7. Tests: No.
8. Logging: No.
9. Dependency Order: config -> contracts -> ... (this file imports contracts.config).

SECURITY POLICY (Section 3):
  - config/settings.py must NEVER be committed to git (.gitignore).
  - This file (settings.example.py) is the safe template with placeholders.
  - If settings.py is ever committed by accident, rotate all credentials immediately.
"""

from contracts.config import SystemConfig

settings = SystemConfig(
    telegram_bot_token="0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
    supabase_url="https://YOUR-PROJECT.supabase.co",
    supabase_key="YOUR-SUPABASE-SERVICE-KEY",
    redis_url="redis://localhost:6379/0",
    default_timeframes=["15m", "1h", "4h"],
    max_active_coins=10,
    simulation_mode=True,
)
