"""通用输入列合并 notes + 名称推断 + progress_callback。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from tools.pipeline.io_excel import merge_extra_columns_into_notes, read_input_xlsx
from tools.pipeline.paths import SCHEMA_EXCEL_IO
from tools.pipeline.runner import _infer_company_name, run_pipeline


def test_merge_extra_into_notes(tmp_path: Path) -> None:
    meta = __import__("json").loads(SCHEMA_EXCEL_IO.read_text(encoding="utf-8"))
    inp = tmp_path / "g.xlsx"
    pd.DataFrame([{"客户代码": "C001", "company_name": "ACME", "website": ""}]).to_excel(inp, index=False, engine="openpyxl")
    df, miss = read_input_xlsx(inp, meta=meta)
    assert not miss
    # 新行为: "客户代码" 优先匹配到 company_name（但已存在则不覆盖），不再随意塞入备注
    assert df.loc[0, "company_name"] == "ACME"


def test_infer_company_from_extra_column() -> None:
    meta = __import__("json").loads(SCHEMA_EXCEL_IO.read_text(encoding="utf-8"))
    row = pd.Series(
        {"company_name": "", "website": "", "evidence_paste": "paste content here", "客商简称": "BetaCorp"}
    )
    name, inferred = _infer_company_name(row, meta=meta, row_index=0)
    assert name == "BetaCorp"
    assert inferred is True


def test_progress_callback_dry_run(tmp_path: Path) -> None:
    inp = tmp_path / "in.xlsx"
    out = tmp_path / "out.xlsx"
    pd.DataFrame([{"company_name": "Z", "evidence_paste": "minimum evidence"}]).to_excel(inp, index=False, engine="openpyxl")
    calls: list[dict] = []

    run_pipeline(
        inp,
        out,
        dry_run=True,
        no_fetch=True,
        excel_io_path=SCHEMA_EXCEL_IO,
        progress_callback=lambda d: calls.append(dict(d)),
    )
    phases = {c.get("phase") for c in calls}
    assert "ready" in phases
    assert "write" in phases
    assert "done" in phases
