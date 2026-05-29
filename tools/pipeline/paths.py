from __future__ import annotations

import os
import sys as _sys
from pathlib import Path


def _detect_repo_root() -> Path:
    if getattr(_sys, "frozen", False):
        return Path(_sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


# 仓库根目录（假设本文件位于 tools/pipeline/）
REPO_ROOT = _detect_repo_root()

SCHEMA_EVAL_RESULT = REPO_ROOT / "schemas" / "eval_result.schema.json"
SCHEMA_EXCEL_IO = REPO_ROOT / "schemas" / "excel_io.json"
DEFAULT_CATALOG_PATH = REPO_ROOT / "output" / "catalog.json"
DEFAULT_KB_PATH = REPO_ROOT / "product_kb" / "v1" / "kb.json"
DEFAULT_CACHE_DIR = REPO_ROOT / "cache" / "fetch"


def resolve_catalog_path(override: Path | None) -> Path:
    if override is not None:
        return override
    e = (os.environ.get("CATALOG_PATH") or "").strip()
    return Path(e) if e else DEFAULT_CATALOG_PATH


def resolve_kb_path(override: Path | None) -> Path:
    if override is not None:
        return override
    e = (os.environ.get("PRODUCT_KB_PATH") or "").strip()
    return Path(e) if e else DEFAULT_KB_PATH


def resolve_cache_dir(override: Path | None) -> Path:
    if override is not None:
        return override
    e = (os.environ.get("CACHE_DIR") or "").strip()
    return Path(e) if e else DEFAULT_CACHE_DIR

