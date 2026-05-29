"""中文表头可直接读入（无需先跑 map_zh）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from tools.pipeline.io_excel import read_input_xlsx


def test_read_zh_customer_columns(tmp_path: Path) -> None:
    inp = tmp_path / "zh.xlsx"
    df_in = pd.DataFrame(
        [
            {
                "客户名称": "测试公司",
                "企业网站": "https://example.com",
                "联系地址": "某路1号",
                "固定电话": "1234567890",
                "联系人邮箱": "a@b.com",
                "联系人姓名": "张三",
            }
        ]
    )
    df_in.to_excel(inp, index=False, engine="openpyxl")

    df, missing = read_input_xlsx(inp)
    assert not missing
    assert df.loc[0, "company_name"] == "测试公司"
    # 联系人信息现在保留在独立列中，不自动拼入备注
    assert df.loc[0, "contact_address"] == "某路1号"
    assert str(df.loc[0, "contact_phone"]) == "1234567890"
    assert df.loc[0, "contact_email"] == "a@b.com"
    assert df.loc[0, "contact_name"] == "张三"
