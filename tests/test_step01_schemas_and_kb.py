"""步骤 1：schema / excel_io / kb 可加载且示例 JSON 符合 LLM schema。"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[1]


def test_eval_result_schema_validates_minimal_example() -> None:
    schema = json.loads((ROOT / "schemas" / "eval_result.schema.json").read_text(encoding="utf-8"))
    instance = {
        "product_fit_score": 3,
        "product_fit_reasons": ["证据显示主营与万用表弱相关"],
        "capability_score": 3,
        "capability_signals": [],
        "reputation_risk": {"facts": [], "concerns": [], "sources": []},
        "reputation_safety_score": 3,
        "buyer_seller_role": "unclear",
        "buyer_seller_reason": "证据中未体现明确采购或供应主身份",
        "deal_recommendation": "watch",
        "next_action": "补全官网产品页后再评",
        "confidence": 0.4,
        "data_quality": "low",
        "citations": [
            {
                "claim": "示例",
                "source_url": "",
                "source_snippet": "（无 URL，来自人工粘贴）",
            }
        ],
        "catalog_version_note": "test",
    }
    jsonschema.validate(instance=instance, schema=schema)


def test_excel_io_loads() -> None:
    data = json.loads((ROOT / "schemas" / "excel_io.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 1
    names = [c["name"] for c in data["input_columns"]]
    assert "company_name" in names and "evidence_paste" in names
    assert "contact_address" in names
    assert len(data.get("summary_export_columns") or []) == 19
    w = data["weights_default"]
    assert abs(w["product_fit"] + w["capability"] + w["reputation_safety"] - 1.0) < 1e-6


def test_kb_loads() -> None:
    kb = json.loads((ROOT / "product_kb" / "v1" / "kb.json").read_text(encoding="utf-8"))
    assert kb.get("kb_version")
    assert kb.get("one_liner_zh")
