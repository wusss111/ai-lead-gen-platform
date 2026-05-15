from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from tools.pipeline.paths import REPO_ROOT


@dataclass(frozen=True)
class InternalWebSettings:
    """环境变量配置（部署时注入）。"""

    data_dir: Path
    redis_url: str
    queue_name: str
    max_upload_mb: int
    max_rows: int
    basic_user: str | None
    basic_password: str | None


def load_settings() -> InternalWebSettings:
    data = (os.environ.get("INTERNAL_WEB_DATA_DIR") or "").strip()
    data_dir = Path(data) if data else (REPO_ROOT / "var" / "internal_web")
    user = (os.environ.get("INTERNAL_WEB_BASIC_USER") or "").strip()
    pw = (os.environ.get("INTERNAL_WEB_BASIC_PASSWORD") or "").strip()
    return InternalWebSettings(
        data_dir=data_dir.resolve(),
        redis_url=(os.environ.get("REDIS_URL") or "redis://127.0.0.1:6379/0").strip(),
        queue_name=(os.environ.get("INTERNAL_WEB_QUEUE_NAME") or "eval_jobs").strip(),
        max_upload_mb=int(os.environ.get("INTERNAL_WEB_MAX_UPLOAD_MB") or "32"),
        max_rows=int(os.environ.get("INTERNAL_WEB_MAX_ROWS") or "500"),
        basic_user=user or None,
        basic_password=pw or None,
    )
