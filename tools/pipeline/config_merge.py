from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def merge_meta_with_file(meta: dict[str, Any], config_path: Path | None) -> dict[str, Any]:
    """用 JSON 配置文件覆盖 meta 中的 weights / review_rules（浅合并）。"""
    out: dict[str, Any] = dict(meta)
    if config_path is None or not config_path.is_file():
        return out
    extra = json.loads(config_path.read_text(encoding="utf-8"))
    if "weights_default" in extra and isinstance(extra["weights_default"], dict):
        base = dict(out.get("weights_default") or {})
        base.update(extra["weights_default"])
        out["weights_default"] = base
    if "review_rules_default" in extra and isinstance(extra["review_rules_default"], dict):
        base = dict(out.get("review_rules_default") or {})
        base.update(extra["review_rules_default"])
        out["review_rules_default"] = base
    return out


def merge_meta_from_env(meta: dict[str, Any]) -> dict[str, Any]:
    raw = (os.environ.get("PIPELINE_CONFIG_PATH") or "").strip()
    if not raw:
        return meta
    return merge_meta_with_file(meta, Path(raw))
