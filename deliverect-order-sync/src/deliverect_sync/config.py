"""Application configuration using Pydantic Settings.

Loads settings from config.yaml with environment variable overrides.
"""

from __future__ import annotations

import os
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator


class PortalType(str, Enum):
    """Deliverect portal type."""

    RESTAURANT = "restaurant"
    ENTERPRISE = "enterprise"


class DateMode(str, Enum):
    """Date range selection mode."""

    ROLLING = "rolling"
    FIXED = "fixed"


class PortalConfig(BaseModel):
    """Portal connection settings."""

    type: PortalType = PortalType.RESTAURANT
    base_url: str = "https://frontend.deliverect.com/"


class ExportConfig(BaseModel):
    """Order export settings."""

    date_mode: DateMode = DateMode.ROLLING
    rolling_days: int = Field(default=3, ge=1, le=365)
    fixed_start: date | None = None
    fixed_end: date | None = None
    timezone: str = "Asia/Riyadh"

    locations: list[str] = Field(default_factory=list)
    channels: list[str] = Field(default_factory=list)
    statuses: list[str] = Field(
        default_factory=lambda: ["Accepted", "Finalized", "Canceled", "Failed"]
    )
    custom_tags: list[str] = Field(default_factory=list)

    requested_fields: list[str] = Field(
        default_factory=lambda: [
            "Pickup time",
            "Location",
            "Order ID",
            "Channel",
            "Delivery System",
            "Receipt ID",
            "Status",
            "Items",
            "PLU",
            "Quantity",
            "Total",
            "Fees",
            "Payment method",
            "Paid",
        ]
    )
    mandatory_fields: list[str] = Field(
        default_factory=lambda: ["Order ID", "Status"]
    )

    split_items_into_separate_rows: bool = True
    format: str = "csv"


class FallbackConfig(BaseModel):
    """Individual order detail fallback settings."""

    order_detail_extraction: bool = False
    maximum_orders_per_run: int = Field(default=100, ge=1, le=10000)


class PrivacyConfig(BaseModel):
    """Customer data privacy settings."""

    include_customer_name: bool = False
    include_customer_phone: bool = False
    include_customer_email: bool = False
    include_delivery_address: bool = False
    raw_download_retention_days: int = Field(default=7, ge=1, le=365)
    retain_raw_csv: bool = False
    retain_encrypted_raw_rows: bool = False


class OutputConfig(BaseModel):
    """Output file settings."""

    directory: str = "./output"
    excel_filename_pattern: str = "deliverect_orders_{date}.xlsx"

    @property
    def resolved_directory(self) -> Path:
        """Resolve the output directory relative to CWD."""
        return Path(self.directory).resolve()


class BrowserConfig(BaseModel):
    """Browser automation settings."""

    headless_for_scheduled: bool = True
    download_timeout: int = Field(default=120, ge=10, le=600)
    navigation_timeout: int = Field(default=30000, ge=5000, le=120000)


class PollingConfig(BaseModel):
    """Export operation polling settings."""

    initial_interval_seconds: int = Field(default=2, ge=1, le=30)
    max_interval_seconds: int = Field(default=15, ge=5, le=120)
    max_wait_seconds: int = Field(default=600, ge=60, le=3600)


class SchedulingConfig(BaseModel):
    """Scheduling and concurrency settings."""

    lock_timeout_seconds: int = Field(default=60, ge=10, le=600)


class DiagnosticsConfig(BaseModel):
    """Diagnostic and observability settings."""

    screenshots_enabled: bool = False
    screenshot_retention_days: int = Field(default=1, ge=1, le=30)
    traces_enabled: bool = False
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return upper


class AppSettings(BaseModel):
    """Root application settings."""

    portal: PortalConfig = Field(default_factory=PortalConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    fallback: FallbackConfig = Field(default_factory=FallbackConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    polling: PollingConfig = Field(default_factory=PollingConfig)
    scheduling: SchedulingConfig = Field(default_factory=SchedulingConfig)
    diagnostics: DiagnosticsConfig = Field(default_factory=DiagnosticsConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> AppSettings:
        """Load settings from a YAML configuration file."""
        config_path = Path(path)
        if not config_path.exists():
            raise FileNotFoundError(
                f"Configuration file not found: {config_path}\n"
                "Copy config.example.yaml to config.yaml and adjust settings."
            )

        with open(config_path, encoding="utf-8") as f:
            raw: dict[str, Any] = yaml.safe_load(f) or {}

        return cls.model_validate(raw)

    @classmethod
    def load(cls, config_path: str | Path | None = None) -> AppSettings:
        """Load settings with fallback discovery.

        Searches for config.yaml in:
        1. Explicit path argument
        2. DELIVERECT_SYNC_CONFIG environment variable
        3. Current working directory
        4. Default settings (no file)
        """
        if config_path:
            return cls.from_yaml(config_path)

        env_path = os.environ.get("DELIVERECT_SYNC_CONFIG")
        if env_path:
            return cls.from_yaml(env_path)

        cwd_path = Path.cwd() / "config.yaml"
        if cwd_path.exists():
            return cls.from_yaml(cwd_path)

        return cls()

    def has_customer_pii_enabled(self) -> bool:
        """Check if any customer PII category is enabled."""
        return any([
            self.privacy.include_customer_name,
            self.privacy.include_customer_phone,
            self.privacy.include_customer_email,
            self.privacy.include_delivery_address,
        ])

    @property
    def data_dir(self) -> Path:
        """Application data directory."""
        base = Path.home() / ".deliverect-sync"
        base.mkdir(parents=True, exist_ok=True)
        return base

    @property
    def auth_dir(self) -> Path:
        """Authentication state directory."""
        d = self.data_dir / "auth"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def db_path(self) -> Path:
        """SQLite database path."""
        return self.data_dir / "deliverect_sync.db"

    @property
    def downloads_dir(self) -> Path:
        """Temporary downloads directory."""
        d = self.data_dir / "downloads"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def logs_dir(self) -> Path:
        """Log files directory."""
        d = self.data_dir / "logs"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @property
    def screenshots_dir(self) -> Path:
        """Diagnostic screenshots directory."""
        d = self.data_dir / "screenshots"
        d.mkdir(parents=True, exist_ok=True)
        return d


def load_selectors(path: str | Path | None = None) -> dict[str, Any]:
    """Load selector definitions from YAML.

    Searches for selectors.yaml in:
    1. Explicit path
    2. DELIVERECT_SYNC_SELECTORS environment variable
    3. Current working directory
    """
    if path:
        sel_path = Path(path)
    else:
        env_path = os.environ.get("DELIVERECT_SYNC_SELECTORS")
        if env_path:
            sel_path = Path(env_path)
        else:
            sel_path = Path.cwd() / "selectors.yaml"

    if not sel_path.exists():
        raise FileNotFoundError(
            f"Selectors file not found: {sel_path}\n"
            "Run 'python -m deliverect_sync calibrate' to generate selectors.yaml,\n"
            "or copy selectors.example.yaml to selectors.yaml."
        )

    with open(sel_path, encoding="utf-8") as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    return data
