from __future__ import annotations

from pathlib import Path

import pandas as pd

from tools.pipeline.runner import run_pipeline


def test_batch_dry_run_append_merges_summary(tmp_path: Path) -> None:
    inp = tmp_path / "in.xlsx"
    out = tmp_path / "out.xlsx"
    rows = [{"company_name": f"C{i}", "evidence_paste": f"evidence line {i} for dry run."} for i in range(5)]
    pd.DataFrame(rows).to_excel(inp, index=False, engine="openpyxl")

    run_pipeline(inp, out, dry_run=True, no_fetch=True, start_row=0, limit=2, append_output=False)
    s1 = pd.read_excel(out, sheet_name="Summary", engine="openpyxl")
    assert len(s1) == 2
    assert list(s1["数据行号"]) == [1, 2]

    run_pipeline(inp, out, dry_run=True, no_fetch=True, start_row=2, limit=2, append_output=True)
    s2 = pd.read_excel(out, sheet_name="Summary", engine="openpyxl")
    assert len(s2) == 4
    assert list(s2["数据行号"]) == [1, 2, 3, 4]

    d2 = pd.read_excel(out, sheet_name="Detail", engine="openpyxl")
    assert len(d2) == 4
