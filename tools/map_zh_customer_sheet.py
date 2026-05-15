# -*- coding: utf-8 -*-
"""将常见中文客户表头映射为流水线输入列，并合并联系信息到 notes。

映射后跑流水线（不抓站、省时间）示例::

  python tools/map_zh_customer_sheet.py 源表.xlsx examples/mapped.xlsx
  python run_customer_pipeline.py --input examples/mapped.xlsx --output examples/out.xlsx --no-fetch
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

_ZH_TO_CONTACT = [
    ("联系地址", "contact_address"),
    ("联系人地址", "contact_address"),
    ("固定电话", "contact_phone"),
    ("联系人邮箱", "contact_email"),
    ("联系人姓名", "contact_name"),
]

_NOTES_LABELS = [
    ("联系地址", "contact_address"),
    ("固定电话", "contact_phone"),
    ("联系人邮箱", "contact_email"),
    ("联系人姓名", "contact_name"),
]


def map_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df = df.loc[:, [c for c in df.columns if c is not None and str(c).strip() != ""]]
    rename = {
        "客户名称": "company_name",
        "企业网站": "website",
        "洲": "country_region",
    }
    out = df.rename(columns=rename)

    for zh, en in _ZH_TO_CONTACT:
        if zh not in out.columns:
            continue
        if en not in out.columns:
            out[en] = out[zh]
        else:
            mask = out[en].astype(str).str.strip() == ""
            out.loc[mask, en] = out.loc[mask, zh]

    zh_drop = [zh for zh, _ in _ZH_TO_CONTACT if zh in out.columns]
    if zh_drop:
        out = out.drop(columns=zh_drop, errors="ignore")

    for col in ("contact_address", "contact_phone", "contact_email", "contact_name"):
        if col not in out.columns:
            out[col] = ""

    def build_notes(row: pd.Series) -> str:
        parts: list[str] = []
        for label, key in _NOTES_LABELS:
            if key not in row.index:
                continue
            v = row.get(key)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            s = str(v).strip()
            if s:
                parts.append(f"{label}: {s}")
        return " | ".join(parts)

    out["notes"] = out.apply(build_notes, axis=1)
    for col in ("target_products", "priority", "evidence_paste"):
        if col not in out.columns:
            out[col] = ""
    for c in ("company_name", "website", "country_region"):
        if c not in out.columns:
            out[c] = ""

    front: list[str] = []
    if "客户代码" in out.columns:
        front.append("客户代码")
    front += [
        "company_name",
        "website",
        "country_region",
        "contact_address",
        "contact_phone",
        "contact_email",
        "contact_name",
        "target_products",
        "priority",
        "notes",
        "evidence_paste",
    ]
    rest = [c for c in out.columns if c not in front]
    return out[front + rest]


def main() -> None:
    ap = argparse.ArgumentParser(description="中文客户表 → 流水线输入 xlsx")
    ap.add_argument("input", type=Path, help="源 xlsx")
    ap.add_argument("output", type=Path, help="输出 xlsx")
    args = ap.parse_args()
    df = pd.read_excel(args.input, engine="openpyxl")
    mapped = map_dataframe(df)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    mapped.to_excel(args.output, index=False, engine="openpyxl")
    print(args.output)


if __name__ == "__main__":
    main()
