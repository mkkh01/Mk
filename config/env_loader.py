"""
Environment variable loader.
Reads from .env file and os.environ. No hardcoded secrets.
"""
import os
from pathlib import Path
from typing import Optional


def _load_dotenv() -> None:
    """Simple .env loader (no external dependency)."""
    env_path = Path(__file__).parent.parent / ".env"
    if not env_path.exists():
        env_path = Path(".env")
    if not env_path.exists():
        return
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


_load_dotenv()


def get_env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def get_env_int(key: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(key, str(default)))
    except ValueError:
        return default


def get_env_float(key: str, default: float = 0.0) -> float:
    try:
        return float(os.environ.get(key, str(default)))
    except ValueError:
        return default


def get_env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key, str(default)).lower()
    return val in ("true", "1", "yes", "on")


def load_env_vars() -> dict:
    """Return all loaded env vars for inspection (secrets masked)."""
    loaded = {}
    for k, v in os.environ.items():
        if any(s in k.upper() for s in ("TOKEN", "KEY", "SECRET", "PASSWORD", "URL")):
            loaded[k] = v[:8] + "..." if len(v) > 8 else "***"
        else:
            loaded[k] = v
    return loaded
