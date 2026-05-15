from __future__ import annotations

from pathlib import Path

import pandas as pd

from tools.pipeline.runner import run_pipeline


def test_dry_run_no_fetch_writes_output(tmp_path: Path) -> None:
    inp = tmp_path / "in.xlsx"
    out = tmp_path / "out.xlsx"
    df = pd.DataFrame(
        [
            {
                "company_name": "ACME",
                "website": "",
                "evidence_paste": "Some pasted evidence for scoring skip in dry run.",
            }
        ]
    )
    df.to_excel(inp, index=False, engine="openpyxl")

    run_pipeline(inp, out, dry_run=True, no_fetch=True, limit=1)
    assert out.is_file()
    got = pd.read_excel(out, sheet_name="Summary", engine="openpyxl")
    assert list(got.columns) == [
        "数据行号",
        "客户名称",
        "网站",
        "国家",
        "联系人地址",
        "固定电话",
        "联系人邮箱",
        "联系人姓名",
        "买方/卖方",
        "角色判断依据",
        "产品匹配说明",
        "产品匹配分",
        "能力评分",
        "能力依据",
        "资信要点",
        "资信安全分",
        "合作建议",
        "需复核",
        "综合分",
    ]
    assert "试运行" in str(got.loc[0, "产品匹配说明"])
    detail = pd.read_excel(out, sheet_name="Detail", engine="openpyxl")
    assert "merged_evidence" in detail.columns and len(detail) == 1
