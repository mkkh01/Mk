"""
Configuration Engine — loading, validating, and serving configuration.
No business logic. No trading decisions.
"""
import logging
from core.base import BaseEngine
from config.settings import get_settings, reload_settings, Settings


class ConfigEngine(BaseEngine):
    """Manages system configuration. Single source of truth for settings."""

    def __init__(self):
        super().__init__("config_engine")
        self.settings: Settings = None
        self.is_valid: bool = False
        self.validation_errors: list[str] = []

    async def initialize(self) -> None:
        self.settings = get_settings()
        self.validation_errors = self.settings.validate()
        self.is_valid = len(self.validation_errors) == 0
        if not self.is_valid:
            self.logger.error(
                f"Configuration validation failed. Missing: {self.validation_errors}"
            )
        else:
            self.logger.info(f"Configuration loaded. {self.settings.mask_secrets()}")

    async def start(self) -> None:
        if not self.is_valid:
            raise RuntimeError(
                f"Cannot start: configuration invalid. Missing: {self.validation_errors}"
            )
        self._running = True

    async def stop(self) -> None:
        self._running = False

    def get(self, key: str, default=None):
        """Get a setting value by attribute name."""
        return getattr(self.settings, key, default)

    def reload(self) -> Settings:
        """Hot-reload configuration."""
        self.settings = reload_settings()
        self.validation_errors = self.settings.validate()
        self.is_valid = len(self.validation_errors) == 0
        self.logger.info("Configuration reloaded.")
        return self.settings
