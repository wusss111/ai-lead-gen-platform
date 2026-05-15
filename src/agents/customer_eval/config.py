"""Customer eval agent configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from tools.pipeline.paths import REPO_ROOT


@dataclass
class CustomerEvalConfig:
    max_upload_mb: int = 32
    max_rows: int = 500
    queue_name: str = "customer_eval:default"
    data_dir: Path = REPO_ROOT / "var" / "platform"
    job_timeout: int = 2700
    result_ttl: int = 86400

    @classmethod
    def from_env(cls) -> "CustomerEvalConfig":
        return cls(
            max_upload_mb=int(os.environ.get("CUSTOMER_EVAL_MAX_UPLOAD_MB", "32")),
            max_rows=int(os.environ.get("CUSTOMER_EVAL_MAX_ROWS", "500")),
            queue_name=os.environ.get("CUSTOMER_EVAL_QUEUE", "customer_eval:default"),
            data_dir=Path(os.environ.get("PLATFORM_DATA_DIR", str(REPO_ROOT / "var" / "platform"))),
        )
