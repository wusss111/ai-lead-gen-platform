"""CRM agent configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class CrmConfig:
    page_size: int = 20
    max_export_rows: int = 5000

    @classmethod
    def from_env(cls) -> "CrmConfig":
        return cls(
            page_size=int(os.environ.get("CRM_PAGE_SIZE", "20")),
            max_export_rows=int(os.environ.get("CRM_MAX_EXPORT_ROWS", "5000")),
        )
