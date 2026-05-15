"""--no-fetch 且仅有 notes（无 evidence_paste）时仍应调用 LLM。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pandas as pd

from tools.pipeline.runner import run_pipeline

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_CAT = ROOT / "tests" / "fixtures" / "minimal_catalog.json"


@patch("tools.pipeline.runner.run_llm_eval")
def test_no_fetch_with_notes_only_calls_llm(mock_llm, tmp_path: Path) -> None:
    mock_llm.return_value = {
        "product_fit_score": 3,
        "product_fit_reasons": ["notes 中有联系信息"],
        "capability_score": 3,
        "capability_signals": [],
        "reputation_risk": {"facts": [], "concerns": [], "sources": []},
        "reputation_safety_score": 3,
        "buyer_seller_role": "unclear",
        "buyer_seller_reason": "notes 信息不足以区分主角色",
        "deal_recommendation": "watch",
        "next_action": "跟进",
        "confidence": 0.5,
        "data_quality": "medium",
        "citations": [{"claim": "x", "source_url": "", "source_snippet": "y"}],
        "catalog_version_note": "t",
    }
    inp = tmp_path / "in.xlsx"
    out = tmp_path / "out.xlsx"
    df = pd.DataFrame(
        [
            {
                "company_name": "NoteOnlyCo",
                "website": "",
                "country_region": "",
                "target_products": "",
                "priority": "",
                "notes": "联系地址: 测试路1号 | 联系人: 张三",
                "evidence_paste": "",
            }
        ]
    )
    df.to_excel(inp, index=False, engine="openpyxl")
    run_pipeline(
        inp,
        out,
        dry_run=False,
        no_fetch=True,
        limit=1,
        catalog_path=FIXTURE_CAT,
    )
    mock_llm.assert_called_once()
    assert out.is_file()
