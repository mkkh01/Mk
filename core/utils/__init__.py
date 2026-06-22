"""
Utility functions shared across the system.
No business logic, no API calls.
"""
import time
import functools
from typing import Callable, Any
import logging

logger = logging.getLogger("utils")


def retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0,
          exceptions: tuple = (Exception,)):
    """Async retry decorator with exponential backoff."""
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc = None
            current_delay = delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_attempts:
                        logger.warning(
                            f"Retry {attempt}/{max_attempts} for {func.__name__}: {e}"
                        )
                        await asyncio_sleep(current_delay)
                        current_delay *= backoff
            raise last_exc
        return wrapper
    return decorator


def safe_float(value: Any, default: float = 0.0) -> float:
    """Convert to float safely."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def truncate_precision(value: float, decimals: int) -> float:
    """Truncate to N decimal places without rounding."""
    factor = 10 ** decimals
    return float(int(value * factor)) / factor


def get_dynamic_precision(price: float) -> int:
    """Determine decimal precision based on price magnitude."""
    if price < 0.0001: return 8
    if price < 0.001: return 7
    if price < 0.01: return 6
    if price < 0.1: return 5
    if price < 1: return 4
    if price < 100: return 3
    return 2


# Lazy import to avoid circular dependency
def asyncio_sleep(seconds: float):
    import asyncio
    return asyncio.sleep(seconds)
