"""Unified platform configuration loaded from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from tools.pipeline.paths import REPO_ROOT


@dataclass
class PlatformConfig:
    """Top-level platform configuration aggregated from env vars."""

    # Platform
    app_title: str = "外贸客户平台"
    app_version: str = "2.0.0"
    redis_url: str = "redis://127.0.0.1:6379/0"
    data_dir: Path = field(default_factory=lambda: REPO_ROOT / "var" / "platform")
    basic_user: str | None = None
    basic_password: str | None = None
    debug: bool = False

    # Agent-specific config dicts (loaded lazily per agent)
    db_path: Path | None = None

    @classmethod
    def from_env(cls) -> "PlatformConfig":
        data_dir = os.environ.get("PLATFORM_DATA_DIR", "")
        data = Path(data_dir) if data_dir else (REPO_ROOT / "var" / "platform")
        user = (os.environ.get("BASIC_USER") or os.environ.get("INTERNAL_WEB_BASIC_USER") or "").strip()
        pw = (os.environ.get("BASIC_PASSWORD") or os.environ.get("INTERNAL_WEB_BASIC_PASSWORD") or "").strip()
        return cls(
            app_title=(os.environ.get("APP_TITLE") or "外贸客户平台").strip(),
            redis_url=(os.environ.get("REDIS_URL") or "redis://127.0.0.1:6379/0").strip(),
            data_dir=data.resolve(),
            basic_user=user or None,
            basic_password=pw or None,
            debug=os.environ.get("DEBUG", "").strip().lower() in ("1", "true", "yes"),
            db_path=data.resolve() / "platform.db",
        )


# Singleton
_platform_config: PlatformConfig | None = None


def get_config() -> PlatformConfig:
    global _platform_config
    if _platform_config is None:
        _platform_config = PlatformConfig.from_env()
    return _platform_config
