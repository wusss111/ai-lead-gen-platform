from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from tools.pipeline.config_merge import merge_meta_from_env, merge_meta_with_file
from tools.pipeline.evidence import merge_scrape_and_paste
from tools.pipeline.fetch_cache import fetch_pages_for_website_field
from tools.pipeline.io_excel import (
    MAIN_SHEET_NAME,
    build_summary_export_df,
    ensure_output_columns,
    load_excel_io,
    output_column_names,
    read_input_xlsx,
    write_result_xlsx,
)
from tools.pipeline.llm_eval import run_llm_eval
from tools.pipeline.paths import (
    resolve_cache_dir,
    resolve_catalog_path,
    resolve_kb_path,
)
from tools.pipeline.scoring import cap_model_data_quality, manual_review_flag, overall_score_computed

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[dict[str, Any]], None]

_SKIP_NAME_INFER_COLS = frozenset(
    {
        "website",
        "evidence_paste",
        "notes",
        "country_region",
        "target_products",
        "priority",
        "contact_address",
        "contact_phone",
        "contact_email",
        "contact_name",
    }
)


def _looks_like_url(s: str) -> bool:
    sl = s.strip().lower()
    return sl.startswith("http://") or sl.startswith("https://") or sl.startswith("www.")


def _infer_company_name(
    row: pd.Series,
    *,
    meta: dict[str, Any],
    row_index: int,
) -> tuple[str, bool]:
    """名称推断：(名称, 是否为推断)。company_name 为空时扫描其它输入列。"""
    base = _cell(row.get("company_name", ""))
    if base:
        return base, False
    output_cols = set(output_column_names(meta))
    for c in row.index:
        if c == "company_name" or c in output_cols or c in _SKIP_NAME_INFER_COLS:
            continue
        v = _cell(row.get(c))
        if not v or len(v) > 500:
            continue
        if _looks_like_url(v):
            continue
        return v, True
    return f"未命名客户-第{row_index + 1}行", True


def _cell(v: Any) -> str:
    if v is None:
        return ""
    try:
        if pd.isna(v):
            return ""
    except TypeError:
        pass
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def _detail_merged_display(merged: str, notes: str) -> str:
    """Detail 表展示用：抓取/粘贴 + notes（便于审计）。"""
    m, n = merged.strip(), notes.strip()
    if m and n:
        return m + "\n\n---\n\n【notes】\n" + n
    return m or n


def _fmt_pages(pages: list[dict[str, Any]]) -> str:
    bits: list[str] = []
    for p in pages:
        st = "ok" if p.get("ok") else "fail"
        c = "cache" if p.get("from_cache") else "net"
        bits.append(f"{p.get('url','')}({st},{c})")
    return "; ".join(bits)


def _flatten_eval(ev: dict[str, Any]) -> dict[str, Any]:
    rr = ev.get("reputation_risk") or {}
    return {
        "product_fit_score": int(ev["product_fit_score"]),
        "product_fit_reasons": "；".join(ev.get("product_fit_reasons") or []),
        "capability_score": int(ev["capability_score"]),
        "capability_signals": "；".join(ev.get("capability_signals") or []),
        "reputation_facts": "；".join(rr.get("facts") or []),
        "reputation_concerns": "；".join(rr.get("concerns") or []),
        "reputation_sources": "；".join(rr.get("sources") or []),
        "reputation_safety_score": int(ev["reputation_safety_score"]),
        "buyer_seller_role": str(ev.get("buyer_seller_role") or "unclear"),
        "buyer_seller_reason": str(ev.get("buyer_seller_reason") or ""),
        "deal_recommendation": str(ev["deal_recommendation"]),
        "next_action": str(ev.get("next_action") or ""),
        "confidence": float(ev.get("confidence") or 0),
        "data_quality": str(ev.get("data_quality") or "low"),
        "eval_json": json.dumps(ev, ensure_ascii=False),
    }


def run_pipeline(
    input_path: Path,
    output_path: Path,
    *,
    dry_run: bool = False,
    no_fetch: bool = False,
    limit: int | None = None,
    start_row: int = 0,
    append_output: bool = False,
    cache_dir: Path | None = None,
    catalog_path: Path | None = None,
    kb_path: Path | None = None,
    excel_io_path: Path | None = None,
    pipeline_config_path: Path | None = None,
    detail_sheet: bool = True,
    highlight_manual_review: bool = True,
    stop_on_error: bool = False,
    progress_callback: ProgressCallback | None = None,
    batch_info_out: dict[str, Any] | None = None,
) -> pd.DataFrame:
    def report(**payload: Any) -> None:
        if progress_callback:
            progress_callback(dict(payload))

    meta = load_excel_io(excel_io_path)
    meta = merge_meta_with_file(meta, pipeline_config_path)
    meta = merge_meta_from_env(meta)
    df, missing_required = read_input_xlsx(input_path, meta=meta)
    if missing_required:
        raise ValueError(f"输入表缺少必填列: {', '.join(missing_required)}")

    df = ensure_output_columns(df, meta)
    weights = meta.get("weights_default")
    rules = meta.get("review_rules_default")
    cache = resolve_cache_dir(cache_dir)
    cat = resolve_catalog_path(catalog_path)
    kb = resolve_kb_path(kb_path)

    if not dry_run and not cat.is_file():
        raise FileNotFoundError(f"缺少产品目录 catalog.json: {cat}（可先运行 tools/build_product_catalog.py 或设置 CATALOG_PATH）")

    n_total = len(df)
    start = max(0, int(start_row))
    if start >= n_total:
        raise ValueError(f"start_row={start} 超出输入行数 {n_total}")
    if limit is None:
        end = n_total
    else:
        end = min(n_total, start + int(limit))
    if end <= start:
        raise ValueError(f"本批无行可处理（start_row={start}, limit={limit}）")

    n_batch = end - start
    report(
        phase="ready",
        current=0,
        total=n_batch,
        message=f"第 {start + 1}-{end} 行（共 {n_total} 行），开始处理",
    )
    detail_rows: list[dict[str, Any]] = []
    for i in range(start, end):
        row = df.iloc[i]
        name, name_inferred = _infer_company_name(row, meta=meta, row_index=i)
        err_parts: list[str] = []
        if name_inferred:
            err_parts.append("客户名称已从未映射列/占位推断，建议核对")
            df.at[i, "company_name"] = name

        website = _cell(row.get("website", ""))
        paste = _cell(row.get("evidence_paste", ""))
        pages: list[dict[str, Any]] = []
        fetch_errs: list[str] = []
        if no_fetch or not website.strip():
            if not website.strip():
                fetch_errs.append("未提供 website")
        else:
            pages, fetch_errs = fetch_pages_for_website_field(website, cache_dir=cache)

        scraped_blocks = [(f"抓取 URL: {p['url']}", str(p.get("text") or "")) for p in pages if p.get("ok")]
        merged = merge_scrape_and_paste(scraped_blocks=scraped_blocks, evidence_paste=paste or None)
        notes_text = _cell(row.get("notes", ""))
        any_fetch_ok = any(bool(p.get("ok")) for p in pages)
        detail_block = _detail_merged_display(merged, notes_text)

        logger.info("处理行 %s/%s（本批 %s/%s）: %s", i + 1, n_total, i - start + 1, n_batch, name)
        report(
            phase="row",
            current=i - start + 1,
            total=n_batch,
            label=name[:120],
            message=f"第 {i + 1}/{n_total} 行（本批 {i - start + 1}/{n_batch}）：{name[:80]}",
        )

        df.at[i, "fetched_pages"] = _fmt_pages(pages)
        df.at[i, "search_fallback_used"] = "no"
        base_err = "; ".join(fetch_errs) if fetch_errs else ""
        if not merged.strip() and not notes_text.strip() and not any_fetch_ok:
            err_parts.append(base_err or "无抓取文本且无 evidence_paste/notes")

        eval_out: dict[str, Any] | None = None
        eval_json_str = ""

        if dry_run:
            err_parts.append("dry_run: 跳过 LLM")
            df.at[i, "product_fit_reasons"] = "说明：本次为试运行（--dry-run），未调用大模型。"
            df.at[i, "errors"] = "; ".join([p for p in [base_err, *err_parts] if p])
            if detail_sheet:
                detail_rows.append(
                    {
                        "row_index": i + 1,
                        "company_name": name,
                        "merged_evidence": detail_block,
                        "eval_json": eval_json_str,
                    }
                )
            continue

        if not merged.strip() and not notes_text.strip():
            err_parts.append("无证据：跳过 LLM（请填写 website、evidence_paste 或 notes）")
            df.at[i, "product_fit_reasons"] = (
                "说明：无可用证据（请填写 website、evidence_paste 或 notes），未调用大模型。"
            )
            df.at[i, "errors"] = "; ".join([p for p in [base_err, *err_parts] if p])
            if detail_sheet:
                detail_rows.append(
                    {
                        "row_index": i + 1,
                        "company_name": name,
                        "merged_evidence": detail_block,
                        "eval_json": eval_json_str,
                    }
                )
            continue

        try:
            eval_out = run_llm_eval(
                merged_evidence=merged,
                company_name=name,
                website=website,
                country_region=_cell(row.get("country_region", "")),
                target_products=_cell(row.get("target_products", "")),
                notes=_cell(row.get("notes", "")),
                catalog_path=cat,
                kb_path=kb,
            )
        except Exception as e:  # noqa: BLE001
            err_parts.append(f"LLM失败: {type(e).__name__}: {e}")
            df.at[i, "product_fit_reasons"] = f"说明：模型调用失败（{type(e).__name__}），请检查密钥与网络后重试。"
            df.at[i, "errors"] = "; ".join([p for p in [base_err, *err_parts] if p])
            if detail_sheet:
                detail_rows.append(
                    {
                        "row_index": i + 1,
                        "company_name": name,
                        "merged_evidence": detail_block,
                        "eval_json": eval_json_str,
                    }
                )
            if stop_on_error:
                raise
            continue

        dq = cap_model_data_quality(
            str(eval_out.get("data_quality") or "low"),
            any_fetch_ok=any_fetch_ok,
            paste_len=len(paste) + len(notes_text),
        )
        eval_out["data_quality"] = dq

        flat = _flatten_eval(eval_out)
        eval_json_str = str(flat.get("eval_json") or "")
        for k, v in flat.items():
            df.at[i, k] = v

        overall = overall_score_computed(
            product_fit_score=int(eval_out["product_fit_score"]),
            capability_score=int(eval_out["capability_score"]),
            reputation_safety_score=int(eval_out["reputation_safety_score"]),
            weights=weights,
        )
        df.at[i, "overall_score_computed"] = overall
        df.at[i, "manual_review_flag"] = manual_review_flag(
            overall=float(overall),
            reputation_safety_score=int(eval_out["reputation_safety_score"]),
            reputation_concerns_text=flat["reputation_concerns"],
            rules=rules,
        )
        df.at[i, "errors"] = "; ".join([p for p in [base_err, *err_parts] if p])

        if detail_sheet:
            detail_rows.append(
                {
                    "row_index": i + 1,
                    "company_name": name,
                    "merged_evidence": detail_block,
                    "eval_json": eval_json_str,
                }
            )

    detail_df = pd.DataFrame(detail_rows) if detail_rows else None
    if not detail_sheet:
        detail_df = None

    batch_df = df.iloc[start:end].copy()
    source_rows_1b = list(range(start + 1, end + 1))
    summary_part = build_summary_export_df(
        batch_df.reset_index(drop=True),
        meta,
        source_row_1based=source_rows_1b,
    )

    old_summary = pd.DataFrame()
    old_detail = pd.DataFrame()
    if append_output and output_path.is_file():
        try:
            old_summary = pd.read_excel(output_path, sheet_name=MAIN_SHEET_NAME, engine="openpyxl")
            if detail_sheet:
                try:
                    old_detail = pd.read_excel(output_path, sheet_name="Detail", engine="openpyxl")
                except ValueError:
                    old_detail = pd.DataFrame()
        except Exception:  # noqa: BLE001
            logger.warning("追加模式读取已有 output 失败，将覆盖写入: %s", output_path, exc_info=True)
            old_summary = pd.DataFrame()
            old_detail = pd.DataFrame()

    if old_summary is not None and not old_summary.empty:
        summary_df = pd.concat([old_summary, summary_part], ignore_index=True, sort=False)
    else:
        summary_df = summary_part

    if detail_sheet and detail_df is not None and not detail_df.empty:
        if old_detail is not None and not old_detail.empty:
            detail_merged = pd.concat([old_detail, detail_df], ignore_index=True, sort=False)
        else:
            detail_merged = detail_df
    else:
        detail_merged = old_detail if (detail_sheet and old_detail is not None and not old_detail.empty) else None

    # Summary 含「需复核」列时由 write_result_xlsx 按列着色；否则不传行号集合
    highlight_rows: set[int] | None = None
    if highlight_manual_review and "需复核" not in summary_df.columns:
        highlight_rows = {
            i
            for i in range(len(summary_df))
            if str(summary_df.iloc[i].get("manual_review_flag", "")).strip().upper() == "YES"
        }

    report(phase="write", current=n_batch, total=n_batch, message="正在写入 Excel…")
    write_result_xlsx(
        summary_df,
        output_path,
        detail_df=detail_merged,
        highlight_manual_review=highlight_manual_review,
        highlight_summary_row_indices=highlight_rows if highlight_manual_review else None,
    )
    has_more = end < n_total
    report(
        phase="done",
        current=n_batch,
        total=n_batch,
        message="本批完成" + ("，仍有剩余行可继续处理" if has_more else ""),
        total_rows=n_total,
        batch_start_row=start,
        batch_end_exclusive=end,
        has_more=has_more,
    )
    if batch_info_out is not None:
        batch_info_out.update(
            {
                "total_rows": n_total,
                "batch_start_row": start,
                "batch_end_exclusive": end,
                "has_more": has_more,
            }
        )
    return df
