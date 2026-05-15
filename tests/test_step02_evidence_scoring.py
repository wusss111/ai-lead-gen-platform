from __future__ import annotations

from pathlib import Path

from tools.pipeline.evidence import merge_scrape_and_paste
from tools.pipeline.paths import SCHEMA_EXCEL_IO
from tools.pipeline.scoring import cap_model_data_quality, manual_review_flag, overall_score_computed


def test_merge_scrape_and_paste_orders_blocks() -> None:
    m = merge_scrape_and_paste(
        scraped_blocks=[("URL: https://a.test", "Hello A")],
        evidence_paste="粘贴补充",
    )
    assert "Hello A" in m and "人工粘贴" in m and "粘贴补充" in m


def test_overall_score_weights() -> None:
    s = overall_score_computed(
        product_fit_score=5,
        capability_score=4,
        reputation_safety_score=3,
    )
    assert abs(s - 4.15) < 1e-6


def test_manual_review_keyword() -> None:
    assert (
        manual_review_flag(
            overall=2.0,
            reputation_safety_score=5,
            reputation_concerns_text="涉及欺诈投诉",
        )
        == "YES"
    )


def test_excel_io_path_exists() -> None:
    assert SCHEMA_EXCEL_IO.is_file()


def test_cap_model_data_quality_short_paste() -> None:
    assert cap_model_data_quality("high", any_fetch_ok=False, paste_len=10) == "medium"
