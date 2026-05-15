"""
从「报价版式」Excel 各 Sheet 抽取型号与 FEATURES 长文本，生成可版本化的产品目录
（JSON + Markdown），供外贸客户评估等流程引用。

用法:
  python tools/build_product_catalog.py --input "路径\\2026万用表新报价系统 采购价格.xlsx"
  或设置环境变量 PRODUCT_CATALOG_XLSX 后省略 --input。

输出默认写入 ./output/catalog.json 与 ./output/catalog.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PRODUCT_LABEL_MARKERS = ("Product Name", "Model NO", "Model NO.")


def _is_product_label(cell: object) -> bool:
    if not isinstance(cell, str):
        return False
    s = cell.replace("\n", " ").strip()
    if "Product Name" not in s:
        return False
    return any(m in s for m in ("Model NO", "Model NO."))


def _clean_model(s: object) -> str | None:
    if not isinstance(s, str):
        return None
    t = s.strip()
    if not t or t.lower() == "nan":
        return None
    return t


def _noise_string(s: str) -> bool:
    u = s.strip()
    if len(u) < 3:
        return True
    low = u.lower()
    if low.startswith("quotation"):
        return True
    if "contact person" in low:
        return True
    if "phone# & e-mail" in low:
        return True
    if u == "FEATURES:" or u.startswith("FEATURES:") and len(u) < 30:
        return True
    if "product photo" in low:
        return True
    return False


def _col_span_same_row(anchors: list[tuple[int, int]], row: int, j: int, ncols: int) -> int:
    """同一行多栏报价时，用「下一个 Product 标签列」限制扫描宽度，避免串到邻栏。"""
    rights = sorted([aj for (ai, aj) in anchors if ai == row and aj > j])
    if rights:
        return max(2, rights[0] - j - 1)
    return min(14, max(2, ncols - j - 1))


def extract_features_block(
    df: pd.DataFrame,
    product_row: int,
    label_col: int,
    col_span: int = 14,
    max_scan_rows: int = 14,
    min_body_len: int = 40,
) -> str | None:
    """在报价块 (label_col 起 col_span 列) 内，取产品说明区域中最长的一段正文。"""
    nrows, ncols = df.shape
    best = ""
    r0 = product_row + 1
    r1 = min(product_row + 1 + max_scan_rows, nrows)
    c1 = min(label_col + col_span, ncols)
    for r in range(r0, r1):
        for c in range(label_col, c1):
            val = df.iat[r, c]
            if not isinstance(val, str):
                continue
            s = val.strip()
            if len(s) < min_body_len:
                continue
            if _noise_string(s):
                continue
            if _is_product_label(s):
                continue
            if len(s) > len(best):
                best = s
    return best.strip() if best else None


def iter_product_anchors(df: pd.DataFrame) -> list[tuple[int, int]]:
    anchors: list[tuple[int, int]] = []
    nrows, ncols = df.shape
    for i in range(nrows):
        for j in range(ncols):
            if _is_product_label(df.iat[i, j]):
                anchors.append((i, j))
    return anchors


def collect_sheet_products(df: pd.DataFrame) -> list[dict]:
    out: list[dict] = []
    anchors = iter_product_anchors(df)
    ncols = df.shape[1]
    for i, j in anchors:
        if j + 1 >= df.shape[1]:
            continue
        model = _clean_model(df.iat[i, j + 1])
        if not model:
            continue
        span = _col_span_same_row(anchors, i, j, ncols)
        feats = extract_features_block(df, i, j, col_span=span)
        out.append({"model": model, "features": feats})
    return out


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_catalog(xlsx_path: Path) -> dict:
    sha = file_sha256(xlsx_path)
    generated = datetime.now(timezone.utc).isoformat()
    xl = pd.ExcelFile(xlsx_path, engine="openpyxl")
    categories: list[dict] = []
    for sheet in xl.sheet_names:
        df = pd.read_excel(xlsx_path, sheet_name=sheet, header=None, engine="openpyxl")
        products = collect_sheet_products(df)
        if not products:
            continue
        # 同 Sheet 内同型号保留第一条（Socket Tester 等重复模板）
        seen: set[str] = set()
        deduped: list[dict] = []
        for p in products:
            key = p["model"].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(p)
        categories.append({"sheet": sheet, "products": deduped})

    return {
        "schema_version": 1,
        "generated_at_utc": generated,
        "source_path": str(xlsx_path.resolve()),
        "source_basename": xlsx_path.name,
        "source_sha256": sha,
        "catalog_version": sha[:12],
        "categories": categories,
    }


def catalog_to_markdown(data: dict) -> str:
    lines: list[str] = []
    lines.append("# 我方产品目录（自 Excel 生成）")
    lines.append("")
    lines.append(f"- **catalog_version**: `{data['catalog_version']}`")
    lines.append(f"- **source_sha256**: `{data['source_sha256']}`")
    lines.append(f"- **generated_at_utc**: {data['generated_at_utc']}")
    lines.append(f"- **source_basename**: {data['source_basename']}")
    lines.append("")
    for cat in data.get("categories", []):
        sheet = cat.get("sheet", "")
        lines.append(f"## {sheet}")
        lines.append("")
        for p in cat.get("products", []):
            model = p.get("model", "")
            lines.append(f"### {model}")
            lines.append("")
            feats = p.get("features")
            if feats:
                lines.append(feats)
            else:
                lines.append("_(未抽取到 FEATURES 正文，请检查版式或扩大解析窗口)_")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def default_input_path() -> Path | None:
    env = os.environ.get("PRODUCT_CATALOG_XLSX", "").strip()
    if env:
        p = Path(env)
        if p.is_file():
            return p
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="从报价版式 xlsx 生成产品目录 JSON/Markdown")
    ap.add_argument(
        "--input",
        "-i",
        type=Path,
        help="采购/报价用产品 Excel 路径；若不填则读环境变量 PRODUCT_CATALOG_XLSX",
    )
    ap.add_argument(
        "--out-dir",
        "-o",
        type=Path,
        default=Path("output"),
        help="输出目录，默认 ./output",
    )
    args = ap.parse_args()
    xlsx = args.input or default_input_path()
    if not xlsx or not xlsx.is_file():
        ap.error(
            "请指定存在的 --input xlsx，或设置环境变量 PRODUCT_CATALOG_XLSX 指向该文件。"
        )

    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    data = build_catalog(xlsx)
    json_path = out_dir / "catalog.json"
    md_path = out_dir / "catalog.md"

    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(catalog_to_markdown(data), encoding="utf-8")

    n = sum(len(c["products"]) for c in data["categories"])
    print(f"已写入 {json_path} 与 {md_path}（共 {len(data['categories'])} 个品类 Sheet，{n} 条型号）")
    print(f"catalog_version={data['catalog_version']}")


if __name__ == "__main__":
    main()
