from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
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
    read_input_csv,
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
ControlCallback = Callable[[], str | None]  # Returns "cancel", "pause", or None
RowSaveCallback = Callable[[int, pd.Series, dict[str, Any]], None]  # (row_idx, row_series, eval_result)

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


def _extract_company_name_from_notes(notes_text: str, website: str = "") -> str:
    """从备注文本中提取可能的公司名称。"""
    if not notes_text:
        return ""
    # 尝试从域名字段推断
    if website:
        domain = re.sub(r'^https?://(?:www\.)?', '', website).split('/')[0]
        # 取域名主体作为备选
        parts = domain.split('.')
        if len(parts) >= 2 and parts[0].lower() not in ('www', 'mail', 'shop', 'store', 'blog'):
            # 尝试从备注中找到与域名相关的公司名
            domain_hint = parts[0]
            for segment in re.split(r'[|；;，,\n]+', notes_text):
                segment = segment.strip()
                if domain_hint.lower() in segment.lower() and len(segment) < 80:
                    # 找到形如 "公司备注: xxx" 的段落后，提取冒号后内容
                    if ':' in segment or '：' in segment:
                        val = re.split(r'[：:]', segment, maxsplit=1)[-1].strip()
                        if val and len(val) < 60:
                            return val
    # 尝试从 Linkedin 字段提取
    m = re.search(r'(?:Linkedin|linkedin)[：:]\s*https?://(?:www\.)?linkedin\.com/company/([^/\s|]+)', notes_text)
    if m:
        return m.group(1).replace('-', ' ').title()
    return ""


def _infer_company_name(
    row: pd.Series,
    *,
    meta: dict[str, Any],
    row_index: int,
) -> tuple[str, bool]:
    """名称推断：(名称, 是否为推断)。company_name 为空或为纯数字时尝试从其他列提取。"""
    base = _cell(row.get("company_name", ""))
    # 纯数字或 URL 不是有效公司名
    if base and not re.match(r"^\d{4,}$", base) and not _looks_like_url(base):
        return base, False

    notes_text = _cell(row.get("notes", ""))
    website = _cell(row.get("website", ""))

    # 尝试从备注中提取
    extracted = _extract_company_name_from_notes(notes_text, website)
    if extracted:
        return extracted, True

    # company_name 为空/纯数字时扫描其它输入列
    output_cols = set(output_column_names(meta))
    for c in row.index:
        if c == "company_name" or c in output_cols or c in _SKIP_NAME_INFER_COLS:
            continue
        v = _cell(row.get(c))
        if not v or len(v) > 500 or _looks_like_url(v) or re.match(r"^\d{4,}$", v):
            continue
        return v, True

    # 最后兜底：无有效公司名
    return f"未命名客户-第{row_index + 1}行", True


def _cell(v: Any) -> str:
    if v is None:
        return ""
    # Guard against pandas Series (can happen with duplicate columns)
    if hasattr(v, "iloc") and hasattr(v, "shape"):
        try:
            if len(v) > 0:
                v = v.iloc[0]
            else:
                return ""
        except Exception:
            pass
    try:
        if pd.isna(v):
            return ""
    except (TypeError, ValueError):
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


def _check_semantic_validity(eval_out: dict[str, Any], err_parts: list[str]) -> None:
    """检测 LLM 输出是否语义上有效（非空壳/敷衍）。"""
    # 所有 reason 字段都是"无法判断"等敷衍内容
    reasons_text = " ".join([
        " ".join(eval_out.get("product_fit_reasons") or []),
        " ".join(eval_out.get("capability_signals") or []),
        str(eval_out.get("buyer_seller_reason") or ""),
        str(eval_out.get("next_action") or ""),
    ])
    generic_phrases = ["无法判断", "无法确定", "无相关信息", "信息不足", "无足够证据", "不确定"]
    all_generic = all(p in reasons_text for p in generic_phrases) or len(reasons_text.strip()) < 20

    confidence = eval_out.get("confidence", 0)
    if isinstance(confidence, (int, float)):
        if confidence < 0.3:
            err_parts.append("评估置信度过低(<0.3)")
            eval_out["data_quality"] = "low"
        if confidence < 0.1:
            eval_out["manual_review_flag_override"] = "YES"

    if all_generic:
        err_parts.append("LLM 输出内容过于空泛（所有判断均为'无法判断'），建议人工复核")
        eval_out["data_quality"] = "low"

    # 评分矛盾检测
    pf = eval_out.get("product_fit_score", 0)
    dr = str(eval_out.get("deal_recommendation", "")).lower()
    if isinstance(pf, (int, float)) and pf >= 4 and dr == "no":
        err_parts.append(f"评分矛盾: product_fit={pf} 但 deal_recommendation=no，需复核")


def _backfill_contacts_from_pages(df: pd.DataFrame, i: int, pages: list[dict[str, Any]], row) -> None:
    """从抓取页面中提取的邮箱/电话回填到 DataFrame 行。
    全部邮箱存入 contact_emails_all（JSON数组），最佳邮箱存入 contact_email。
    """
    contacts = {}
    for p in pages:
        if p.get("ok") and p.get("extracted_contacts"):
            contacts = p["extracted_contacts"]
            break
    if not contacts:
        return

    all_emails = contacts.get("emails", [])
    if not all_emails:
        return

    current_email = _cell(row.get("contact_email", ""))

    # 全部邮箱（JSON 数组）→ contact_emails_all
    import json as _json
    df.at[i, "contact_emails_all"] = _json.dumps(all_emails, ensure_ascii=False)

    # 最佳邮箱 → contact_email（当前值为空或无效时）
    best = all_emails[0]  # 已按相关性排序
    if not current_email or "@" not in current_email:
        df.at[i, "contact_email"] = best

    # 回填电话
    current_phone = _cell(row.get("contact_phone", ""))
    if (not current_phone or len(re.sub(r"\D", "", current_phone)) < 7) and contacts.get("phones"):
        df.at[i, "contact_phone"] = contacts["phones"][0]


def _backfill_social_from_pages(df: pd.DataFrame, i: int, pages: list[dict]) -> None:
    """从抓取页面中提取社交媒体链接，写入 social_profiles 列。"""
    if "social_profiles" not in df.columns:
        return
    import json as _json

    # fetch_pages_for_website_field 已在内部提取 social links，存入 extracted_social
    social_links = []
    for p in pages:
        if p.get("ok") and p.get("extracted_social"):
            social_links = p["extracted_social"]
            break
    df.at[i, "social_profiles"] = _json.dumps(social_links, ensure_ascii=False) if social_links else ""


class _ControlExit(Exception):
    """Signal to stop the pipeline early (cancel or pause)."""
    def __init__(self, reason: str, row: int):
        super().__init__(reason)
        self.reason = reason
        self.row = row


def _save_partial(
    df: pd.DataFrame,
    start: int,
    end: int,
    meta: dict[str, Any],
    detail_rows: list[dict[str, Any]],
    output_path: Path,
    append_output: bool,
    detail_sheet: bool,
    highlight_manual_review: bool,
) -> None:
    """Save partially processed rows to output Excel so no work is lost."""
    batch_df = df.iloc[start:end].copy()
    source_rows_1b = list(range(start + 1, end + 1))
    summary_part = build_summary_export_df(
        batch_df.reset_index(drop=True), meta, source_row_1based=source_rows_1b,
    )
    detail_df = pd.DataFrame(detail_rows) if detail_rows and detail_sheet else None

    # Merge with existing output if appending
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
        except Exception:
            old_summary = pd.DataFrame()
            old_detail = pd.DataFrame()

    if not old_summary.empty:
        summary_df = pd.concat([old_summary, summary_part], ignore_index=True, sort=False)
    else:
        summary_df = summary_part

    if detail_sheet and detail_df is not None and not detail_df.empty:
        detail_merged = pd.concat([old_detail, detail_df], ignore_index=True, sort=False) if not old_detail.empty else detail_df
    else:
        detail_merged = old_detail if (detail_sheet and not old_detail.empty) else None

    write_result_xlsx(
        summary_df, output_path, detail_df=detail_merged,
        highlight_manual_review=highlight_manual_review,
    )
    logger.info("Partial save: rows %d-%d (%d rows) written to %s", start + 1, end, end - start, output_path)


def _write_progress(output_path: Path, next_row: int, total: int, batch_info_out: dict[str, Any] | None) -> None:
    """Write progress.json for later resume."""
    import json as _json
    prog_path = output_path.parent / "progress.json"
    bs = 0
    if batch_info_out:
        bs = batch_info_out.get("batch_size", 0)
    _json_data = {
        "total_rows": total,
        "next_start_row": next_row,
        "batch_size": bs if bs > 0 else 50,
        "has_more": next_row < total,
    }
    prog_path.write_text(_json.dumps(_json_data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Progress saved: next_start_row=%s total=%s", next_row, total)


# ── 并行加速 ──

_PREFETCH_WORKERS = 8   # 预抓取网站并发数
_LLM_WORKERS = 4        # 并行 LLM 评估并发数


def _prefetch_all_websites(
    df: pd.DataFrame,
    start: int,
    end: int,
    *,
    no_fetch: bool,
    cache_dir: Path,
    control_callback: ControlCallback | None = None,
) -> dict[str, tuple[list[dict], list[str]]]:
    """预抓取所有行的唯一网站，返回 {website: (pages, errors)}。
    已在缓存中的网站极快（<0.1s），未缓存的并行抓取。"""
    websites: list[tuple[int, str]] = []
    seen: set[str] = set()
    for i in range(start, end):
        ws = _cell(df.iloc[i].get("website", ""))
        if ws and ws not in seen:
            seen.add(ws)
            websites.append((i, ws))
    if not websites or no_fetch:
        return {}

    logger.info("预抓取 %d 个唯一网站 (%d workers)...", len(websites), _PREFETCH_WORKERS)
    cache: dict[str, tuple[list[dict], list[str]]] = {}
    lock = threading.Lock()

    _prefetch_cancelled = False
    _prefetch_lock = threading.Lock()

    def _fetch_one(ws: str) -> tuple[str, list[dict], list[str]]:
        nonlocal _prefetch_cancelled
        with _prefetch_lock:
            if _prefetch_cancelled:
                return (ws, [], ["已取消"])
        if control_callback:
            signal = control_callback()
            if signal in ("cancel", "pause"):
                with _prefetch_lock:
                    _prefetch_cancelled = True
                return (ws, [], [f"任务已{signal}"])
        pages, errs = fetch_pages_for_website_field(ws, cache_dir=cache_dir)
        return (ws, pages, errs)

    with ThreadPoolExecutor(max_workers=_PREFETCH_WORKERS) as pool:
        futures: dict[Any, str] = {pool.submit(_fetch_one, ws): ws for _, ws in websites}
        # 轮询等待，每 0.5 秒检查取消信号，支持即时中断
        import time as _time
        pending = set(futures.keys())
        while pending:
            done, pending = wait(pending, timeout=0.5, return_when="FIRST_COMPLETED")
            for f in done:
                ws, pages, errs = f.result()
                with lock:
                    cache[ws] = (pages, errs)
            with _prefetch_lock:
                if _prefetch_cancelled:
                    for f in pending:
                        f.cancel()
                    break

    ok_count = sum(1 for pages, _ in cache.values() if any(p.get("ok") for p in pages))
    logger.info("预抓取完成: %d/%d 个网站成功", ok_count, len(cache))
    if _prefetch_cancelled and control_callback:
        signal = control_callback()
        if signal in ("cancel", "pause"):
            raise _ControlExit(signal, start)
    return cache


def _eval_rows_parallel(
    llm_tasks: list[dict],
    control_callback: ControlCallback | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict[int, dict[str, Any]]:
    """并行调用 LLM 评估多行。返回 {row_idx: eval_result}。"""
    if not llm_tasks:
        return {}

    results: dict[int, dict[str, Any]] = {}
    lock = threading.Lock()
    _eval_cancelled = False
    _eval_lock = threading.Lock()
    _eval_done_count = [0]  # 用 list 包装以在闭包中修改
    _eval_total = len(llm_tasks)

    def _eval_one(task: dict) -> tuple[int, dict[str, Any]]:
        nonlocal _eval_cancelled
        i = task["i"]
        with _eval_lock:
            if _eval_cancelled:
                return (i, {"_error": "已取消"})
        try:
            if control_callback:
                signal = control_callback()
                if signal in ("cancel", "pause"):
                    with _eval_lock:
                        _eval_cancelled = True
                    return (i, {"_error": f"任务已{signal}"})
            eval_out = run_llm_eval(
                merged_evidence=task["merged"],
                company_name=task["name"],
                website=task["website"],
                country_region=task["country_region"],
                target_products=task["target_products"],
                notes=task["notes"],
                catalog_path=task["cat"],
                kb_path=task["kb"],
            )
            return (i, eval_out)
        except Exception as e:
            logger.exception("行 %d (%s) LLM 评估失败", i + 1, task["name"])
            return (i, {"_error": f"{type(e).__name__}: {e}"})

    logger.info("并行 LLM 评估 %d 行 (%d workers)...", len(llm_tasks), _LLM_WORKERS)
    with ThreadPoolExecutor(max_workers=_LLM_WORKERS) as pool:
        futures = {pool.submit(_eval_one, t): t["i"] for t in llm_tasks}
        import time as _time
        pending = set(futures.keys())
        while pending:
            done, pending = wait(pending, timeout=0.5, return_when="FIRST_COMPLETED")
            for f in done:
                i, result = f.result()
                with lock:
                    results[i] = result
                    _eval_done_count[0] += 1
                if progress_callback:
                    progress_callback(_eval_done_count[0], _eval_total)
            with _eval_lock:
                if _eval_cancelled:
                    for f in pending:
                        f.cancel()
                    break

    ok = sum(1 for r in results.values() if "_error" not in r)
    logger.info("并行 LLM 完成: %d/%d 成功", ok, len(results))
    return results


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
    control_callback: ControlCallback | None = None,
    batch_info_out: dict[str, Any] | None = None,
    row_save_callback: RowSaveCallback | None = None,
) -> pd.DataFrame:
    def report(**payload: Any) -> None:
        if progress_callback:
            progress_callback(dict(payload))

    meta = load_excel_io(excel_io_path)
    meta = merge_meta_with_file(meta, pipeline_config_path)
    meta = merge_meta_from_env(meta)
    if input_path.suffix.lower() == ".csv":
        df, missing_required = read_input_csv(input_path, meta=meta)
    else:
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
# ═══ 多线程逐行处理：抓取 → 评估 → 入库（每行独立，原子操作） ═══
    _ROW_WORKERS = 4  # 并发处理行数
    detail_rows: list[dict[str, Any]] = []
    _batch_evaluated: dict[tuple[str, str], dict[str, Any]] = {}  # B5 去重缓存
    _batch_eval_lock = threading.Lock()
    _df_lock = threading.Lock()
    _detail_lock = threading.Lock()
    _cancel_flag = [False]
    _cancel_lock = threading.Lock()
    _row_done = [0]
    _row_done_lock = threading.Lock()
    _row_errors: list[str] = []

    class _RowCtx:
        """一行评估所需的全部上下文。"""
        __slots__ = ("i", "name", "name_inferred", "website", "paste", "pages", "fetch_errs",
                     "merged", "notes_text", "any_fetch_ok", "detail_block",
                     "base_err", "err_parts", "dedup_key", "eval_out", "eval_json_str",
                     "country_region", "target_products", "row_notes")

    def _process_one_row(i: int) -> None:
        """完整处理一行：抓取网站 → 分类筛选 → LLM 评估 → 入库。线程安全。"""
        if _cancel_flag[0]:
            return
        row = df.iloc[i]
        ctx = _RowCtx()
        ctx.i = i
        ctx.err_parts = []
        ctx.eval_out = None
        ctx.eval_json_str = ""

        try:
            # ══ 1. 抓取网站 ══
            name, name_inferred = _infer_company_name(row, meta=meta, row_index=i)
            ctx.name = name
            ctx.name_inferred = name_inferred
            if name_inferred:
                ctx.err_parts.append("客户名称已从未映射列/占位推断，建议核对")

            website = _cell(row.get("website", ""))
            ctx.website = website
            ctx.paste = _cell(row.get("evidence_paste", ""))
            ctx.country_region = _cell(row.get("country_region", ""))
            ctx.target_products = _cell(row.get("target_products", ""))
            ctx.row_notes = _cell(row.get("notes", ""))

            fetch_errs: list[str] = []
            pages: list[dict[str, Any]] = []

            if control_callback:
                signal = control_callback()
                if signal in ("cancel", "pause"):
                    with _cancel_lock:
                        _cancel_flag[0] = True
                    raise _ControlExit(signal, i)

            if no_fetch or not website.strip():
                if not website.strip():
                    fetch_errs.append("未提供 website")
            else:
                try:
                    pages, fetch_errs = fetch_pages_for_website_field(website, cache_dir=cache)
                except Exception as _fetch_exc:
                    logger.exception("行 %d (%s) 网站抓取异常: %s", i + 1, name, website)
                    pages = []
                    fetch_errs = [f"网站抓取异常: {type(_fetch_exc).__name__}: {_fetch_exc}"]
            ctx.pages = pages
            ctx.fetch_errs = fetch_errs

            scraped_blocks = [(f"抓取 URL: {p['url']}", str(p.get("text") or "")) for p in pages if p.get("ok")]
            merged, was_truncated = merge_scrape_and_paste(scraped_blocks=scraped_blocks, evidence_paste=ctx.paste or None)
            if was_truncated:
                ctx.err_parts.append("证据文本过长已截断，评估可能不完整")
            ctx.merged = merged
            ctx.notes_text = _cell(row.get("notes", ""))
            ctx.any_fetch_ok = any(bool(p.get("ok")) for p in pages)
            ctx.detail_block = _detail_merged_display(merged, ctx.notes_text)
            ctx.base_err = "; ".join(fetch_errs) if fetch_errs else ""
            ctx.dedup_key = (name.strip().lower(), website.strip().lower())

        except _ControlExit:
            raise
        except Exception as _prep_exc:
            logger.exception("行 %d 预处理异常: %s", i + 1, _prep_exc)
            ctx.name = _cell(row.get("company_name", "")) or f"未命名客户-第{i + 1}行"
            ctx.name_inferred = False
            ctx.website = _cell(row.get("website", ""))
            ctx.paste = ""
            ctx.country_region = ""
            ctx.target_products = ""
            ctx.row_notes = ""
            ctx.pages = []
            ctx.fetch_errs = [f"预处理异常: {type(_prep_exc).__name__}: {_prep_exc}"]
            ctx.merged = ""
            ctx.notes_text = ""
            ctx.any_fetch_ok = False
            ctx.detail_block = ""
            ctx.base_err = ctx.fetch_errs[0]
            ctx.dedup_key = (ctx.name.strip().lower(), ctx.website.strip().lower())
            ctx.err_parts.append(f"预处理异常: {type(_prep_exc).__name__}")

        # ══ 2. 在线程安全的条件下立即写入基本信息 ══
        with _df_lock:
            df.at[i, "fetched_pages"] = _fmt_pages(ctx.pages)
            _backfill_social_from_pages(df, i, ctx.pages)
            df.at[i, "company_name"] = ctx.name
            df.at[i, "search_fallback_used"] = "no"
            if ctx.err_parts and not ctx.merged.strip():
                df.at[i, "manual_review_flag"] = "YES"
                df.at[i, "data_quality"] = "low"
            if row_save_callback:
                row_save_callback(i, df.iloc[i], {"name": ctx.name, "inferred": ctx.name_inferred})

        # ══ 3. 分类筛选 ══
        # B3: 无效公司名
        if ctx.name.startswith("未命名客户-") or not ctx.name.strip():
            ctx.err_parts.append("缺少有效公司名称，无法评估")
            with _df_lock:
                df.at[i, "product_fit_reasons"] = "说明：缺少有效公司名称，无法调用大模型评估。"
                df.at[i, "errors"] = "; ".join([p for p in [ctx.base_err, *ctx.err_parts] if p])
                df.at[i, "manual_review_flag"] = "YES"
                df.at[i, "data_quality"] = "low"
                if row_save_callback:
                    row_save_callback(i, df.iloc[i], {"name": ctx.name, "inferred": ctx.name_inferred})
        elif not ctx.merged.strip() and not ctx.notes_text.strip() and not ctx.any_fetch_ok:
            ctx.err_parts.append(ctx.base_err or "无抓取文本且无 evidence_paste/notes")

        # B5: 同公司去重
        is_dup = False
        with _batch_eval_lock:
            if ctx.dedup_key in _batch_evaluated and ctx.dedup_key != ("", ""):
                prev_eval = _batch_evaluated[ctx.dedup_key]
                is_dup = True
        if is_dup:
            with _df_lock:
                for _k, _v in prev_eval.items():
                    if _k in ("eval_json",):
                        df.at[i, _k] = _v
                    elif _k in df.columns:
                        df.at[i, _k] = _v
                df.at[i, "product_fit_reasons"] = str(prev_eval.get("product_fit_reasons", "")) + "（复用同公司评估结果）"
                df.at[i, "errors"] = "; ".join([p for p in [ctx.base_err, *ctx.err_parts, "复用同公司结果"] if p])
                if row_save_callback:
                    row_save_callback(i, df.iloc[i], {"name": ctx.name, "inferred": ctx.name_inferred})
            with _detail_lock:
                if detail_sheet:
                    detail_rows.append({"row_index": i + 1, "company_name": ctx.name,
                                        "merged_evidence": ctx.detail_block, "eval_json": prev_eval.get("eval_json", "")})
            logger.info("去重: %s 复用已评估结果", ctx.name)
            _mark_done()
            return

        # Dry run / No evidence
        if dry_run or (not ctx.merged.strip() and not ctx.notes_text.strip()):
            reason = "dry_run: 跳过 LLM" if dry_run else "无证据：跳过 LLM"
            ctx.err_parts.append(reason)
            with _df_lock:
                df.at[i, "product_fit_reasons"] = f"说明：{reason}。"
                df.at[i, "errors"] = "; ".join([p for p in [ctx.base_err, *ctx.err_parts] if p])
                if row_save_callback:
                    row_save_callback(i, df.iloc[i], {"name": ctx.name, "inferred": ctx.name_inferred})
            with _detail_lock:
                if detail_sheet:
                    detail_rows.append({"row_index": i + 1, "company_name": ctx.name,
                                        "merged_evidence": ctx.detail_block, "eval_json": ""})
            _mark_done()
            return

        # ══ 4. LLM 评估（单行） ══
        report(phase="eval", current=_row_done[0] + 1, total=n_batch,
               label=ctx.name[:120], message=f"AI 评估 {ctx.name[:30]} · 第 {_row_done[0] + 1}/{n_batch} 行")

        try:
            eval_out = run_llm_eval(
                merged_evidence=ctx.merged,
                company_name=ctx.name,
                website=ctx.website,
                country_region=ctx.country_region,
                target_products=ctx.target_products,
                notes=ctx.row_notes,
                catalog_path=cat,
                kb_path=kb,
            )
        except Exception as e:
            logger.exception("行 %d (%s) LLM 评估失败", i + 1, ctx.name)
            eval_out = {"_error": f"{type(e).__name__}: {e}"}

        ctx.eval_out = eval_out

        # ══ 5. 后处理 + 写入 DB ══
        if "_error" in eval_out:
            logger.error("行 %d (%s) LLM 失败: %s", i + 1, ctx.name, eval_out["_error"])
            ctx.err_parts.append(f"LLM 失败: {eval_out['_error']}")
            with _df_lock:
                df.at[i, "product_fit_reasons"] = f"说明：LLM 评估失败（{eval_out['_error']}），已跳过。"
                df.at[i, "errors"] = "; ".join([p for p in [ctx.base_err, *ctx.err_parts] if p])
                if row_save_callback:
                    row_save_callback(i, df.iloc[i], {"name": ctx.name, "inferred": ctx.name_inferred})
            if stop_on_error:
                with _cancel_lock:
                    _cancel_flag[0] = True
                raise RuntimeError(f"行 {i + 1} LLM 评估失败: {eval_out['_error']}")
        else:
            try:
                max_fetch_text = max((len(p.get("text", "").strip()) for p in ctx.pages if p.get("ok")), default=0)
                dq = cap_model_data_quality(
                    str(eval_out.get("data_quality") or "low"),
                    any_fetch_ok=ctx.any_fetch_ok,
                    paste_len=len(ctx.paste) + len(ctx.notes_text),
                    max_fetch_text_len=max_fetch_text,
                )
                eval_out["data_quality"] = dq
                _check_semantic_validity(eval_out, ctx.err_parts)

                flat = _flatten_eval(eval_out)
                ctx.eval_json_str = str(flat.get("eval_json") or "")

                overall = overall_score_computed(
                    product_fit_score=int(eval_out["product_fit_score"]),
                    capability_score=int(eval_out["capability_score"]),
                    reputation_safety_score=int(eval_out["reputation_safety_score"]),
                    weights=weights,
                )

                with _df_lock:
                    for _k, _v in flat.items():
                        df.at[i, _k] = _v
                    _backfill_contacts_from_pages(df, i, ctx.pages, df.iloc[i])
                    df.at[i, "overall_score_computed"] = overall
                    df.at[i, "manual_review_flag"] = manual_review_flag(
                        overall=float(overall),
                        reputation_safety_score=int(eval_out["reputation_safety_score"]),
                        reputation_concerns_text=flat["reputation_concerns"],
                        rules=rules,
                    )
                    df.at[i, "errors"] = "; ".join([p for p in [ctx.base_err, *ctx.err_parts] if p])
                    if row_save_callback:
                        row_save_callback(i, df.iloc[i], {"name": ctx.name, "inferred": ctx.name_inferred})

                with _batch_eval_lock:
                    _batch_evaluated[ctx.dedup_key] = {
                        "product_fit_score": int(eval_out["product_fit_score"]),
                        "product_fit_reasons": flat["product_fit_reasons"],
                        "capability_score": int(eval_out["capability_score"]),
                        "capability_signals": flat["capability_signals"],
                        "reputation_safety_score": int(eval_out["reputation_safety_score"]),
                        "deal_recommendation": str(eval_out["deal_recommendation"]),
                        "confidence": float(eval_out.get("confidence") or 0),
                        "data_quality": str(eval_out.get("data_quality") or "low"),
                        "overall_score_computed": overall,
                        "manual_review_flag": str(df.at[i, "manual_review_flag"]),
                        "eval_json": ctx.eval_json_str,
                    }

                with _detail_lock:
                    if detail_sheet:
                        detail_rows.append({"row_index": i + 1, "company_name": ctx.name,
                                            "merged_evidence": ctx.detail_block, "eval_json": ctx.eval_json_str})
            except Exception as e:
                logger.exception("行 %d (%s) 后处理失败", i + 1, ctx.name)
                ctx.err_parts.append(f"后处理失败: {type(e).__name__}: {e}")
                with _df_lock:
                    df.at[i, "product_fit_reasons"] = f"说明：后处理失败（{type(e).__name__}），已跳过。"
                    df.at[i, "errors"] = "; ".join([p for p in [ctx.base_err, *ctx.err_parts] if p])
                    if row_save_callback:
                        row_save_callback(i, df.iloc[i], {"name": ctx.name, "inferred": ctx.name_inferred})
                if stop_on_error:
                    with _cancel_lock:
                        _cancel_flag[0] = True
                    raise

        _mark_done()

    def _mark_done() -> None:
        with _row_done_lock:
            _row_done[0] += 1
            done = _row_done[0]
        if done % 5 == 0 or done == n_batch:
            report(phase="fetch", current=done, total=n_batch,
                   message=f"逐行处理中 · 第 {done}/{n_batch} 行")

    # ── 启动多线程处理 ──
    report(phase="fetch", current=0, total=n_batch, message=f"开始逐行处理 ({_ROW_WORKERS} 线程并发)…")
    _cancel_flag[0] = False

    with ThreadPoolExecutor(max_workers=_ROW_WORKERS) as pool:
        futures = [pool.submit(_process_one_row, i) for i in range(start, end)]

        for f in futures:
            try:
                f.result()
            except _ControlExit as e:
                with _cancel_lock:
                    _cancel_flag[0] = True
                for rem in futures:
                    if not rem.done():
                        rem.cancel()
                done_count = e.end_row - start if hasattr(e, 'end_row') else _row_done[0]
                _save_partial(df, start, _row_done[0] + start, meta, detail_rows, output_path,
                              append_output, detail_sheet, highlight_manual_review)
                report(phase="done", current=_row_done[0], total=n_batch,
                       message=f"已{e.signal}", control=e.signal)
                if batch_info_out is not None:
                    batch_info_out.update({"total_rows": n_total, "batch_start_row": start,
                                           "batch_end_exclusive": _row_done[0] + start,
                                           "has_more": True, "control": e.signal})
                if e.signal == "pause":
                    _write_progress(output_path, _row_done[0] + start, n_total, batch_info_out)
                raise
            except Exception as e:
                logger.exception("行处理线程异常: %s", e)
                _row_errors.append(str(e))

    # ═══ 后处理：输出 Excel ═══
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
