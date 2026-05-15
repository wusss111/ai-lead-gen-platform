from __future__ import annotations

import json
from pathlib import Path

from tools.pipeline.config_merge import merge_meta_with_file


def test_merge_weights(tmp_path: Path) -> None:
    meta = {"weights_default": {"product_fit": 0.45, "capability": 0.25, "reputation_safety": 0.3}}
    cfg = tmp_path / "c.json"
    cfg.write_text(json.dumps({"weights_default": {"product_fit": 0.5}}), encoding="utf-8")
    m = merge_meta_with_file(meta, cfg)
    assert m["weights_default"]["product_fit"] == 0.5
    assert m["weights_default"]["capability"] == 0.25
