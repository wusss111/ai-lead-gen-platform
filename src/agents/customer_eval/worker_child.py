# -*- coding: utf-8 -*-
"""子进程入口：接收主进程数据，逐行 AI 评估 + 实时入库。

通过 multiprocessing.Process 启动，与主进程完全隔离。
子进程退出后 OS 自动回收全部内存，彻底解决 pymalloc arena 累积问题。

数据通过 JSON 文件传入（避免 pickle 大数据），进度通过 stdout JSON 行协议报告给主进程。
"""

from __future__ import annotations

import json as _json
import logging
import os
import re
import sys
import threading
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# 子进程需要独立加载 .env
_env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
if _env_path.is_file():
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(_env_path)

_LLM_WORKERS = 5
_PLAYWRIGHT_THREAD_TIMEOUT = 30  # Playwright 单次硬超时（秒）

# 子进程内全局缓存：每批只需加载一次 catalog + kb + schema
_cached_catalog: dict | None = None
_cached_catalog_path: object = None  # 用 object 做 sentinel，避免 None 歧义
_cached_kb: dict | None = None
_cached_kb_path: object = None
_cached_schema: dict | None = None


def _try_playwright_single(
    url: str, cache_dir: Path, timeout_sec: float = 30.0,
) -> tuple[list[dict], list[str]]:
    """在独立 daemon 线程中跑 Playwright，硬超时防止子进程被拖死。"""
    result_container: list = []

    def _pw_thread() -> None:
        try:
            from tools.pipeline.fetch_cache import fetch_pages_for_website_field
            pages, errs = fetch_pages_for_website_field(
                url, cache_dir=cache_dir, max_pages=3,
                timeout_sec=min(timeout_sec, 30), skip_playwright=False,
            )
            result_container.append((pages, errs))
        except Exception:
            pass

    t = threading.Thread(target=_pw_thread, daemon=True)
    t.start()
    t.join(timeout=min(timeout_sec, _PLAYWRIGHT_THREAD_TIMEOUT))
    if result_container:
        return result_container[0]
    return [], ["Playwright 超时"]


def _cell(v: Any) -> str:
    if v is None:
        return ""
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


def _report_progress(done: int, total: int, name: str = "") -> None:
    """通过 stdout 给主进程发进度（JSON 行协议）。"""
    try:
        msg = _json.dumps({"type": "row", "done": done, "total": total, "name": name[:60]},
                          ensure_ascii=False)
        sys.stdout.write(msg + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def run_child_batch(args_path: str) -> int:
    """子进程主函数。返回评估成功的行数。

    args_path: JSON 文件路径，包含：
        df_slice_json, prefetch_cache_json, meta_json,
        data_root, batch_id, start_row,
        catalog_path, kb_path, skip_playwright
    """
    with open(args_path, "r", encoding="utf-8") as f:
        args = _json.load(f)

    import io
    df_slice = pd.read_json(io.StringIO(args["df_slice_json"]), orient="split")
    prefetch_cache = args.get("prefetch_cache", {})
    if not prefetch_cache:
        try:
            prefetch_cache = _json.loads(args.get("prefetch_cache_json", "{}"))
        except (TypeError, _json.JSONDecodeError):
            prefetch_cache = {}
    meta = _json.loads(args.get("meta_json", "{}"))
    meta = meta or {}

    root = Path(args["data_root"])
    batch_id = args["batch_id"]
    start_row = int(args.get("start_row", 0))
    catalog_path = Path(args["catalog_path"]) if args.get("catalog_path") else None
    kb_path = Path(args["kb_path"]) if args.get("kb_path") else None
    skip_playwright = bool(args.get("skip_playwright", False))

    # 使用主进程传入的 cache_dir
    cache_dir = Path(args["cache_dir"]) if args.get("cache_dir") else (root / ".." / ".." / "cache" / "fetch").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)

    from src.core.database import get_db
    from tools.pipeline.evidence import merge_scrape_and_paste
    from tools.pipeline.fetch_cache import fetch_pages_for_website_field
    from tools.pipeline.llm_eval import run_llm_eval, load_json as _load_json
    from tools.pipeline.runner import (
        _flatten_eval, _fmt_pages, _infer_company_name,
    )

    # 预加载 catalog + kb + schema（整个子进程生命周期只加载一次）
    _catalog = None
    _kb = None
    _catalog_version = ""
    if catalog_path and catalog_path.is_file():
        _catalog = _load_json(catalog_path)
        _catalog_version = str(_catalog.get("catalog_version", ""))
    if kb_path and kb_path.is_file():
        _kb = _load_json(kb_path)
    # 触发 schema 缓存加载（避免每行都读磁盘）
    from tools.pipeline.llm_eval import _eval_schema as _preload_schema
    _preload_schema()

    db = get_db()

    n_batch = len(df_slice)
    done = 0
    _report_progress(0, n_batch, "启动中...")

    def _process_row(idx: int) -> dict[str, Any]:
        row = df_slice.iloc[idx]
        result: dict[str, Any] = {"idx": idx, "skip": False, "reason": ""}

        name, name_inferred = _infer_company_name(row, meta=meta, row_index=start_row + idx)
        website = _cell(row.get("website", ""))
        paste = _cell(row.get("evidence_paste", ""))
        country = _cell(row.get("country_region", ""))
        target = _cell(row.get("target_products", ""))
        notes = _cell(row.get("notes", ""))
        result["name"] = name
        result["name_inferred"] = name_inferred
        result["website"] = website

        # 1. 获取网站数据
        if not website.strip():
            pages, fetch_errs = [], ["未提供 website"]
        else:
            cached = prefetch_cache.get(website)
            if cached is not None:
                if isinstance(cached, dict) and "pages" in cached:
                    pages = cached["pages"]
                    fetch_errs = cached.get("errors", [])
                elif isinstance(cached, (list, tuple)) and len(cached) == 2:
                    pages, fetch_errs = cached
                else:
                    pages, fetch_errs = [], ["缓存格式异常"]
            else:
                try:
                    pages, fetch_errs = fetch_pages_for_website_field(
                        website, cache_dir=cache_dir, max_pages=5, timeout_sec=10.0,
                    )
                except Exception as e:
                    pages, fetch_errs = [], [f"抓取失败: {e}"]

        # Playwright fallback
        if not skip_playwright and website.strip():
            has_text = any(
                p.get("ok") and len(str(p.get("text", "")).strip()) > 200
                for p in pages
            )
            if not has_text and pages:
                try:
                    pw_pages, pw_errs = _try_playwright_single(
                        website, cache_dir=cache_dir, timeout_sec=30.0,
                    )
                    if pw_pages and any(p.get("ok") for p in pw_pages):
                        pages, fetch_errs = pw_pages, pw_errs
                except Exception:
                    pass

        result["pages"] = pages
        result["fetch_errs"] = fetch_errs

        # 2. 合并证据
        scraped = [(f"抓取 URL: {p['url']}", str(p.get("text") or ""))
                   for p in pages if p.get("ok")]
        merged, _ = merge_scrape_and_paste(
            scraped_blocks=scraped, evidence_paste=paste or None,
        )
        notes_text = notes or ""
        result["merged"] = merged

        # 3. 分类筛选
        err_parts = list(fetch_errs) if fetch_errs else []
        if name_inferred:
            err_parts.append("客户名称已从其他列推断，建议核对")

        if name.startswith("未命名客户-") or not name.strip():
            result["skip"] = True
            result["reason"] = "缺少有效公司名称，无法评估"
            result["errors"] = "; ".join(err_parts) if err_parts else result["reason"]
            return result

        any_fetch = any(p.get("ok") for p in pages)
        if not merged.strip() and not notes_text.strip() and not any_fetch:
            result["skip"] = True
            result["reason"] = "无抓取文本且无 evidence_paste/notes"
            err_parts.insert(0, result["reason"])
            result["errors"] = "; ".join(err_parts)
            return result

        # 4. LLM 评估
        try:
            eval_kwargs = dict(
                merged_evidence=merged, company_name=name, website=website,
                country_region=country, target_products=target, notes=notes_text,
                catalog_data=_catalog, kb_data=_kb, evidence_max_chars=8000,
            )
            if catalog_path is not None:
                eval_kwargs["catalog_path"] = catalog_path
            if kb_path is not None:
                eval_kwargs["kb_path"] = kb_path
            eval_out = run_llm_eval(**eval_kwargs)
        except Exception as e:
            result["skip"] = True
            result["reason"] = f"LLM 评估失败: {e}"
            err_parts.append(result["reason"])
            result["errors"] = "; ".join(err_parts)
            return result

        result["eval"] = eval_out
        result["errors"] = "; ".join(err_parts) if err_parts else ""
        return result

    # ── 5 线程并行评估 + 逐行入库 + 单行超时保护 ──
    _ROW_TIMEOUT = 180  # 单行最多 3 分钟，超时自动跳过
    import threading as _threading
    def _process_with_timeout(idx: int) -> dict[str, Any]:
        """在独立线程中运行 _process_row，超时返回跳过结果"""
        result_container: list[dict] = []
        def _work():
            try:
                result_container.append(_process_row(idx))
            except Exception as e:
                result_container.append({
                    "idx": idx, "skip": True,
                    "reason": f"处理异常: {e}",
                    "name": "", "website": "", "pages": [],
                    "fetch_errs": ["异常"], "merged": "", "errors": str(e),
                })
        t = _threading.Thread(target=_work, daemon=True)
        t.start()
        t.join(_ROW_TIMEOUT)
        if result_container:
            return result_container[0]
        return {
            "idx": idx, "skip": True,
            "reason": f"单行处理超时 ({_ROW_TIMEOUT}s)，已自动跳过",
            "name": str(df_slice.iloc[idx].get("company_name", "")),
            "website": str(df_slice.iloc[idx].get("website", "")),
            "pages": [], "fetch_errs": ["超时"],
            "merged": "", "errors": f"超时 {_ROW_TIMEOUT}s",
        }

    with ThreadPoolExecutor(max_workers=_LLM_WORKERS) as pool:
        futures = {pool.submit(_process_with_timeout, i): i for i in range(n_batch)}

        for f in as_completed(futures):
            result = f.result()
            idx = result["idx"]
            row = df_slice.iloc[idx]

            if result.get("skip"):
                _save_skipped_row(db, batch_id, start_row + idx, row, result)
            else:
                _save_eval_row(db, batch_id, start_row + idx, row, result, meta)

            done += 1
            _report_progress(done, n_batch, result.get("name", ""))

    return done


def _extract_contacts_from_pages(pages: list[dict], row_email: str = "", row_phone: str = "") -> dict[str, str]:
    """从抓取页面中提取邮箱和电话（对齐原始 _backfill_contacts_from_pages 行为）。
    只在当前值无效时才覆盖：Excel 已有有效数据优先。
    """
    contacts = {}
    for p in pages:
        if p.get("ok") and p.get("extracted_contacts"):
            contacts = p["extracted_contacts"]
            break
    if not contacts:
        return {}
    result: dict[str, str] = {}
    emails = contacts.get("emails", [])
    if emails:
        result["contact_emails_all"] = _json.dumps(emails, ensure_ascii=False)
        if not row_email or "@" not in row_email:
            result["contact_email"] = emails[0]
    phones = contacts.get("phones", [])
    if phones:
        current_phone = re.sub(r"\D", "", row_phone)
        if not current_phone or len(current_phone) < 7:
            result["contact_phone"] = phones[0]
    return result


def _save_skipped_row(
    db: Any, batch_id: str, row_index: int, row: pd.Series, result: dict,
) -> None:
    """保存跳过的行（无公司名/无证据/LLM失败）。"""
    from tools.pipeline.runner import _fmt_pages

    name = result.get("name", _cell(row.get("company_name", "")))
    pages = result.get("pages", [])
    contacts = _extract_contacts_from_pages(
        pages, _cell(row.get("contact_email", "")), _cell(row.get("contact_phone", ""))
    )
    data = {
        "company_name": name,
        "website": result.get("website", _cell(row.get("website", ""))),
        "contact_email": _cell(row.get("contact_email", "")),
        "contact_phone": _cell(row.get("contact_phone", "")),
        "country_region": _cell(row.get("country_region", "")),
        "fetched_pages": _fmt_pages(pages),
        "product_fit_reasons": f"说明：{result.get('reason', '跳过')}。",
        "errors": result.get("errors", result.get("reason", "")),
        "manual_review_flag": "YES",
        "data_quality": "low",
        **contacts,
    }
    _insert_or_replace(db, batch_id, row_index, data)


def _save_eval_row(
    db: Any, batch_id: str, row_index: int, row: pd.Series, result: dict, meta: dict,
) -> None:
    """保存正常评估的行。"""
    from tools.pipeline.runner import (
        _flatten_eval, _fmt_pages,
    )
    from tools.pipeline.scoring import (
        cap_model_data_quality, manual_review_flag, overall_score_computed,
    )

    ev = result["eval"]
    pages = result.get("pages", [])
    flat = _flatten_eval(ev)

    weights = meta.get("weights_default")
    rules = meta.get("review_rules_default")
    overall = overall_score_computed(
        product_fit_score=int(ev["product_fit_score"]),
        capability_score=int(ev["capability_score"]),
        reputation_safety_score=int(ev["reputation_safety_score"]),
        weights=weights,
    )

    paste_len = len(_cell(row.get("evidence_paste", ""))) + len(_cell(row.get("notes", "")))
    max_fetch = max((len(str(p.get("text", ""))) for p in pages if p.get("ok")), default=0)
    dq = cap_model_data_quality(
        str(ev.get("data_quality") or "low"),
        any_fetch_ok=any(p.get("ok") for p in pages),
        paste_len=paste_len,
        max_fetch_text_len=max_fetch,
    )
    flat["data_quality"] = dq

    contacts = _extract_contacts_from_pages(
        pages, _cell(row.get("contact_email", "")), _cell(row.get("contact_phone", ""))
    )
    data = {
        "company_name": result.get("name", _cell(row.get("company_name", ""))),
        "website": result.get("website", _cell(row.get("website", ""))),
        "country_region": _cell(row.get("country_region", "")),
        "contact_name": _cell(row.get("contact_name", "")),
        "contact_email": _cell(row.get("contact_email", "")),
        "contact_phone": _cell(row.get("contact_phone", "")),
        "contact_address": _cell(row.get("contact_address", "")),
        "target_products": _cell(row.get("target_products", "")),
        "notes": _cell(row.get("notes", "")),
        "fetched_pages": _fmt_pages(pages),
        **flat,
        "overall_score_computed": overall,
        "manual_review_flag": manual_review_flag(
            overall=float(overall),
            reputation_safety_score=int(ev["reputation_safety_score"]),
            reputation_concerns_text=flat["reputation_concerns"],
            rules=rules,
        ),
        "errors": result.get("errors", ""),
        **contacts,
    }
    _insert_or_replace(db, batch_id, row_index, data)


def _insert_or_replace(db: Any, batch_id: str, row_index: int, data: dict) -> None:
    """INSERT OR REPLACE 一行到 customer 表。"""
    cols = [
        "batch_id", "row_index", "company_name", "website",
        "country_region", "contact_name", "contact_email", "contact_phone",
        "contact_address", "target_products", "notes",
        "product_fit_score", "product_fit_reasons",
        "capability_score", "capability_signals",
        "reputation_facts", "reputation_concerns", "reputation_sources",
        "reputation_safety_score",
        "buyer_seller_role", "buyer_seller_reason",
        "deal_recommendation", "next_action",
        "confidence", "data_quality",
        "fetched_pages", "errors",
        "overall_score_computed", "manual_review_flag", "eval_json",
        "contact_emails_all",
    ]
    values = [batch_id, row_index + 1]
    for c in cols[2:]:
        v = data.get(c)
        if isinstance(v, float) and pd.isna(v):
            v = None
        values.append(v)
    placeholders = ",".join("?" for _ in cols)
    sql = f"INSERT OR REPLACE INTO customer ({','.join(cols)}) VALUES ({placeholders})"
    db.execute(sql, values)
    db.commit()


def _update_rows_completed(db: Any, batch_id: str, count: int) -> None:
    """每 10 行更新 evaluation_batch.rows_completed。
    只增不减，避免跨批次覆盖（父进程会后加累进值）。
    """
    if count % 10 == 0:
        db.execute(
            "UPDATE evaluation_batch SET rows_completed=? WHERE id=? AND rows_completed < ?",
            (count, batch_id, count),
        )
        db.commit()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python worker_child.py <args_json_path>", file=sys.stderr)
        sys.exit(1)
    done = run_child_batch(sys.argv[1])
    sys.exit(0 if done > 0 else 1)
