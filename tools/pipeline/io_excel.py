from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tools.pipeline.paths import SCHEMA_EXCEL_IO

# 中文表头 / 别名 → 规范 input 列名（大小写不敏感匹配走 canonical）
COLUMN_ALIASES_TO_CANON: dict[str, str] = {
    # 与 tools/map_zh_customer_sheet.py 一致 + 常见变体（表格尽量通用）
    "客户名称": "company_name",
    "公司名称": "company_name",
    "客户公司": "company_name",
    "商户名称": "company_name",
    "客户": "company_name",
    "买方": "company_name",
    "卖方": "company_name",
    "company": "company_name",
    "company name": "company_name",
    "customer": "company_name",
    "customer name": "company_name",
    "client": "company_name",
    "client name": "company_name",
    "帐户名称": "company_name",
    "账号名称": "company_name",
    "企业网站": "website",
    "网址": "website",
    "官网": "website",
    "网站": "website",
    "homepage": "website",
    "url": "website",
    "web": "website",
    "link": "website",
    "洲": "country_region",
    "国家地区": "country_region",
    "region": "country_region",
    "备注": "notes",
    "说明": "notes",
    "留言": "notes",
    "remark": "notes",
    "remarks": "notes",
    "粘贴证据": "evidence_paste",
    "证据摘录": "evidence_paste",
    "补充说明": "evidence_paste",
    "联系地址": "contact_address",
    "联系人地址": "contact_address",
    "固定电话": "contact_phone",
    "电话": "contact_phone",
    "联系人邮箱": "contact_email",
    "邮箱": "contact_email",
    "邮件": "contact_email",
    "联系人姓名": "contact_name",
    "联系人": "contact_name",
}

_NOTES_FROM_CONTACTS: list[tuple[str, str]] = [
    ("联系地址", "contact_address"),
    ("固定电话", "contact_phone"),
    ("联系人邮箱", "contact_email"),
    ("联系人姓名", "contact_name"),
]


def load_excel_io(path: Path | None = None) -> dict[str, Any]:
    p = path or SCHEMA_EXCEL_IO
    return json.loads(p.read_text(encoding="utf-8"))


def canonical_input_names(meta: dict[str, Any]) -> list[str]:
    return [c["name"] for c in meta["input_columns"]]


def _maybe_fill_notes_from_contacts(df: pd.DataFrame) -> pd.DataFrame:
    """notes 为空时，由联系人列拼接（与 map_zh 脚本行为一致）。"""
    if "notes" not in df.columns:
        return df

    def build_notes(row: pd.Series) -> str:
        cur = row.get("notes")
        if cur is not None and not (isinstance(cur, float) and pd.isna(cur)):
            s0 = str(cur).strip()
            if s0:
                return s0
        parts: list[str] = []
        for label, key in _NOTES_FROM_CONTACTS:
            if key not in row.index:
                continue
            v = row.get(key)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            s = str(v).strip()
            if s:
                parts.append(f"{label}: {s}")
        return " | ".join(parts)

    out = df.copy()
    out["notes"] = out.apply(build_notes, axis=1)
    return out


def normalize_column_map(columns: list[str], canonical: list[str]) -> dict[str, str]:
    """原始列名 -> 规范列名（大小写不敏感 + 中文别名）。"""
    lower_to_canon = {c.lower(): c for c in canonical}
    m: dict[str, str] = {}
    for col in columns:
        key = col.strip()
        if key in COLUMN_ALIASES_TO_CANON:
            m[col] = COLUMN_ALIASES_TO_CANON[key]
            continue
        if key.lower() in lower_to_canon:
            m[col] = lower_to_canon[key.lower()]
    return m


def read_input_xlsx(path: Path, *, meta: dict[str, Any] | None = None) -> tuple[pd.DataFrame, list[str]]:
    meta = meta or load_excel_io()
    canon = canonical_input_names(meta)
    df = pd.read_excel(path, engine="openpyxl")
    cmap = normalize_column_map([str(c) for c in df.columns], canon)
    df = df.rename(columns=cmap)
    for c in meta["input_columns"]:
        if c["name"] in df.columns:
            continue
        if not c.get("required"):
            df[c["name"]] = ""
    df = _maybe_fill_notes_from_contacts(df)
    df = merge_extra_columns_into_notes(df, meta)
    missing = [c["name"] for c in meta["input_columns"] if c.get("required") and c["name"] not in df.columns]
    return df, missing


def merge_extra_columns_into_notes(df: pd.DataFrame, meta: dict[str, Any]) -> pd.DataFrame:
    """将未映射到规范输入名的其它列并入 notes，便于模型利用任意表格字段。"""
    canon = set(canonical_input_names(meta))
    extras = [str(c) for c in df.columns if str(c) not in canon]
    if not extras:
        return df

    def augment(row: pd.Series) -> str:
        parts: list[str] = []
        cur = row.get("notes")
        if cur is not None and not (isinstance(cur, float) and pd.isna(cur)):
            s0 = str(cur).strip()
            if s0:
                parts.append(s0)
        for c in extras:
            v = row.get(c)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                continue
            s = str(v).strip()
            if s:
                parts.append(f"{c}: {s}")
        return " | ".join(parts)

    out = df.copy()
    out["notes"] = out.apply(augment, axis=1)
    return out


def output_column_names(meta: dict[str, Any]) -> list[str]:
    return [c["name"] for c in meta["output_columns_append"]]


def ensure_output_columns(df: pd.DataFrame, meta: dict[str, Any]) -> pd.DataFrame:
    """按 excel_io 中的 type 初始化列，避免整列填 \"\" 导致 pandas 3 字符串 dtype 无法接受整数评分。"""
    idx = df.index
    for col in meta.get("output_columns_append") or []:
        name = col["name"]
        if name in df.columns:
            continue
        typ = col.get("type", "string")
        if typ == "int":
            df[name] = pd.Series(pd.NA, index=idx, dtype="Int64")
        elif typ == "float":
            df[name] = pd.Series(np.nan, index=idx, dtype="float64")
        else:
            df[name] = ""
    return df


def deal_recommendation_display_zh(raw: str) -> str:
    s = str(raw or "").strip().lower()
    if s == "high_intent":
        return "高意向跟进"
    if s == "watch":
        return "观察"
    if s == "no":
        return "不建议深入"
    return raw or ""


def buyer_seller_role_display_zh(raw: str) -> str:
    """LLM 枚举 buyer/seller/both/unclear → Summary 中文列。"""
    s = str(raw or "").strip().lower()
    if s == "buyer":
        return "买方"
    if s == "seller":
        return "卖方"
    if s == "both":
        return "兼营/难区分"
    if s == "unclear":
        return "不明"
    return raw or ""


def summary_export_spec(meta: dict[str, Any]) -> list[tuple[str, str]]:
    """(内部字段名, Excel 列标题)。"""
    spec = meta.get("summary_export_columns")
    if not isinstance(spec, list) or not spec:
        return [
            ("_source_row_1", "数据行号"),
            ("company_name", "客户名称"),
            ("website", "网站"),
            ("country_region", "国家"),
            ("contact_address", "联系人地址"),
            ("contact_phone", "固定电话"),
            ("contact_email", "联系人邮箱"),
            ("contact_name", "联系人姓名"),
            ("buyer_seller_role_display", "买方/卖方"),
            ("buyer_seller_reason", "角色判断依据"),
            ("product_fit_reasons", "产品匹配说明"),
            ("product_fit_score", "产品匹配分"),
            ("capability_score", "能力评分"),
            ("capability_signals", "能力依据"),
            ("reputation_facts", "资信要点"),
            ("reputation_safety_score", "资信安全分"),
            ("deal_recommendation_display", "合作建议"),
            ("manual_review_flag", "需复核"),
            ("overall_score_computed", "综合分"),
        ]
    out: list[tuple[str, str]] = []
    for item in spec:
        if isinstance(item, dict) and item.get("field") and item.get("header"):
            out.append((str(item["field"]), str(item["header"])))
    return out


def _cell_export(v: Any) -> Any:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except TypeError:
        pass
    return v


def build_summary_export_df(
    df: pd.DataFrame,
    meta: dict[str, Any],
    *,
    source_row_1based: list[int] | None = None,
) -> pd.DataFrame:
    """从内部宽表生成对外 Summary 数据框（列由 summary_export_columns 定义）。

    ``source_row_1based`` 与 ``df`` 行一一对应；用于分批导出时标注原始输入表行号（1-based）。
    未传时 ``_source_row_1`` 列默认为当前导出块内序号 1..n。
    """
    spec = summary_export_spec(meta)
    int_fields = {"product_fit_score", "capability_score", "reputation_safety_score"}
    rows: list[dict[str, Any]] = []
    for j, (_, row) in enumerate(df.iterrows()):
        out_row: dict[str, Any] = {}
        for field, header in spec:
            if field == "deal_recommendation_display":
                raw = row.get("deal_recommendation", "")
                val = deal_recommendation_display_zh(str(raw))
            elif field == "buyer_seller_role_display":
                raw = row.get("buyer_seller_role", "")
                val = buyer_seller_role_display_zh(str(raw))
            elif field == "_source_row_1":
                if source_row_1based is not None and j < len(source_row_1based):
                    val = int(source_row_1based[j])
                else:
                    val = j + 1
            else:
                v = _cell_export(row.get(field, ""))
                if field == "overall_score_computed":
                    if v == "":
                        val = ""
                    else:
                        try:
                            val = round(float(v), 3)
                        except (TypeError, ValueError):
                            val = v
                elif field in int_fields and v != "":
                    try:
                        val = int(float(v))
                    except (TypeError, ValueError):
                        val = v
                else:
                    val = v
            out_row[header] = val
        rows.append(out_row)
    return pd.DataFrame(rows, columns=[h for _, h in spec])


MAIN_SHEET_NAME = "Summary"


def write_result_xlsx(
    df: pd.DataFrame,
    path: Path,
    *,
    detail_df: pd.DataFrame | None = None,
    highlight_manual_review: bool = True,
    main_sheet_name: str = MAIN_SHEET_NAME,
    highlight_summary_row_indices: set[int] | None = None,
) -> None:
    """写出 Summary；可选 Detail；并对需复核行做整行浅红填充。

    highlight_summary_row_indices: 0-based 行号（与 df 行对齐），用于 Summary 无 manual_review 列时仍可按内部规则着色。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name=main_sheet_name, index=False)
        if detail_df is not None and not detail_df.empty:
            detail_df.to_excel(writer, sheet_name="Detail", index=False)

    if not highlight_manual_review:
        return

    from openpyxl import load_workbook
    from openpyxl.styles import PatternFill

    wb = load_workbook(path)
    if main_sheet_name not in wb.sheetnames:
        return
    ws = wb[main_sheet_name]
    fill = PatternFill(start_color="FFE6E6", end_color="FFE6E6", fill_type="solid")

    headers = [c.value for c in ws[1]]
    flag_header = None
    for name in ("需复核", "manual_review_flag"):
        if name in headers:
            flag_header = name
            break

    if flag_header is not None:
        col_idx = headers.index(flag_header) + 1
        for row in range(2, ws.max_row + 1):
            flag_cell = ws.cell(row=row, column=col_idx)
            val = str(flag_cell.value or "").strip().upper()
            if val == "YES":
                for c in ws[row]:
                    c.fill = fill
        wb.save(path)
        return

    if highlight_summary_row_indices is not None:
        for i in highlight_summary_row_indices:
            excel_row = i + 2
            if 2 <= excel_row <= ws.max_row:
                for c in ws[excel_row]:
                    c.fill = fill

    wb.save(path)
