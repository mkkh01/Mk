"""
Settings — unified configuration object.
Defaults are hardcoded (like old config.py), overridable by .env.
"""
import os
from dataclasses import dataclass, field
from typing import Optional
from .env_loader import get_env, get_env_int, get_env_float

# ── Hardcoded defaults (identical to old config.py) ─────────
_DEFAULT_TELEGRAM_TOKEN = "8913262863:AAFgwkszxhOLpE4IX874HrOKUb1FZUZwYSo"
_DEFAULT_DATABASE_URL = "postgresql://postgres.lvvcbqqtjygqlxyhiabm:C%2CTWTpTrK%2B7%23mp.@aws-1-eu-central-1.pooler.supabase.com:5432/postgres"
_DEFAULT_ADMIN_ID = 1503808643
_DEFAULT_BINANCE_WS = "wss://stream.binance.com:9443"
_DEFAULT_CAPITAL = 10.0
_DEFAULT_TRADE_FEE = 0.001
_DEFAULT_MAX_RISK = 0.02
_DEFAULT_PORT = 8080


@dataclass
class DatabaseSettings:
    """Database connection settings."""
    raw_url: str = ""
    url: str = ""

    @classmethod
    def from_config(cls, default_url: str) -> "DatabaseSettings":
        raw = get_env("DATABASE_URL", default_url)
        url = raw.replace("postgresql://", "postgresql+asyncpg://", 1)
        return cls(raw_url=raw, url=url)


@dataclass
class Settings:
    """Top-level settings. Hardcoded defaults with .env override."""
    telegram_token: str = ""
    admin_id: int = 0
    database: DatabaseSettings = field(default_factory=lambda: DatabaseSettings())
    binance_ws_url: str = ""
    default_capital: float = 0.0
    trade_fee: float = 0.0
    max_risk_per_trade: float = 0.0
    port: int = 8080
    debug: bool = False

    @classmethod
    def load(cls) -> "Settings":
        return cls(
            telegram_token=get_env("TELEGRAM_TOKEN", _DEFAULT_TELEGRAM_TOKEN),
            admin_id=get_env_int("ADMIN_ID", _DEFAULT_ADMIN_ID),
            database=DatabaseSettings.from_config(_DEFAULT_DATABASE_URL),
            binance_ws_url=get_env("BINANCE_WS_URL", _DEFAULT_BINANCE_WS),
            default_capital=get_env_float("DEFAULT_CAPITAL", _DEFAULT_CAPITAL),
            trade_fee=get_env_float("TRADE_FEE", _DEFAULT_TRADE_FEE),
            max_risk_per_trade=get_env_float("MAX_RISK_PER_TRADE", _DEFAULT_MAX_RISK),
            port=get_env_int("PORT", _DEFAULT_PORT),
            debug=get_env("DEBUG", "false").lower() == "true",
        )

    def validate(self) -> list[str]:
        """Validate required settings. Returns list of missing items."""
        missing = []
        if not self.telegram_token:
            missing.append("TELEGRAM_TOKEN")
        if not self.database.raw_url:
            missing.append("DATABASE_URL")
        return missing

    def mask_secrets(self) -> dict:
        """Return settings dict with secrets masked (for logging)."""
        d = {
            "admin_id": self.admin_id,
            "binance_ws_url": self.binance_ws_url,
            "default_capital": self.default_capital,
            "trade_fee": self.trade_fee,
            "max_risk_per_trade": self.max_risk_per_trade,
            "port": self.port,
            "debug": self.debug,
        }
        if self.telegram_token:
            d["telegram_token"] = self.telegram_token[:8] + "***"
        if self.database.raw_url:
            d["database_url"] = self.database.raw_url[:20] + "***"
        return d


# ── Singleton accessor ──────────────────────────────────────
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings.load()
    return _settings


def reload_settings() -> Settings:
    global _settings
    _settings = Settings.load()
    return _settings
