"""RQ Worker task: run customer evaluation pipeline, save results to SQLite."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd

from src.core.database import get_db

# Ensure .env is loaded for worker processes
from dotenv import load_dotenv as _load_dotenv
_env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
if _env_path.is_file():
    _load_dotenv(_env_path)

logger = logging.getLogger(__name__)

_PROGRESS_ROW_MIN_INTERVAL_S = 1.2


def _df_from_partial(input_path: Path, output_path: Path, start_row: int, end_row: int):
    """Read the partially-processed DataFrame slice from output Excel or input."""
    if output_path.is_file():
        try:
            return pd.read_excel(output_path, engine="openpyxl")
        except Exception:
            pass
    if input_path.suffix.lower() == ".csv":
        from tools.pipeline.io_excel import read_input_csv
        df, _ = read_input_csv(input_path)
    else:
        from tools.pipeline.io_excel import read_input_xlsx
        df, _ = read_input_xlsx(input_path)
    return df.iloc[start_row:end_row]


def run_eval_job(
    folder_job_id: str,
    data_root: str,
    *,
    dry_run: bool = False,
    no_fetch: bool = False,
    limit: int | None = None,
    start_row: int = 0,
    batch_size: int | None = None,
    append_output: bool = False,
    input_ext: str = ".xlsx",
) -> dict[str, Any]:
    """Run eval pipeline, write output.xlsx, and save results to SQLite.
    Supports .xlsx and .csv inputs (detected via input_ext param).
    """
    root = Path(data_root)
    job_dir = root / "jobs" / folder_job_id
    inp_xlsx = job_dir / "input.xlsx"
    inp_csv = job_dir / "input.csv"
    inp = inp_csv if inp_csv.is_file() else inp_xlsx
    out = job_dir / "output.xlsx"
    if not inp.is_file():
        raise FileNotFoundError(f"Input file not found: {inp}")

    if start_row > 0:
        append_output = True

    if batch_size is not None:
        eff_batch = max(1, int(batch_size))
    elif limit is not None:
        eff_batch = max(1, int(limit))
    else:
        eff_batch = 300  # 默认每批 300 行，安全内存上限

    from rq import get_current_job

    from tools.pipeline.runner import run_pipeline, _ControlExit

    last_row_save = {"t": 0.0}

    def rq_progress(payload: dict[str, Any]) -> None:
        job = get_current_job()
        if job is None:
            return
        phase = payload.get("phase")
        if phase == "row":
            now = time.monotonic()
            if now - last_row_save["t"] < _PROGRESS_ROW_MIN_INTERVAL_S:
                return
            last_row_save["t"] = now
        try:
            job.meta["progress"] = payload
            job.save_meta()
        except Exception:
            logger.debug("job.save_meta skipped", exc_info=True)

    # Redis-based control signal checker
    from redis import Redis as _Redis
    _redis_url_val = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    _ctrl_conn = _Redis.from_url(_redis_url_val)

    def check_control() -> str | None:
        """Called before each row. Returns 'cancel', 'pause', or None."""
        try:
            val = _ctrl_conn.get(f"job_control:{folder_job_id}")
            if val:
                return val.decode("utf-8") if isinstance(val, bytes) else val
        except Exception:
            pass
        return None

    batch_info: dict[str, Any] = {}
    logger.info(
        "RQ folder=%s start: input=%s start_row=%s batch=%s append=%s",
        folder_job_id, inp, start_row, eff_batch, append_output,
    )

    # 检查是否为断点续跑
    db = get_db()
    batch_status = db.execute(
        "SELECT status, rows_completed FROM evaluation_batch WHERE id=?",
        (folder_job_id,)
    ).fetchone()
    is_resume = batch_status and batch_status["status"] == "started" and (batch_status["rows_completed"] or 0) > 0
    if is_resume:
        resume_from = batch_status["rows_completed"]
        logger.info("断点续跑: batch=%s 从第 %d 行继续", folder_job_id, resume_from + 1)
        start_row = resume_from
        append_output = True

    row_saver = _make_row_saver(folder_job_id, inp.name, resume=is_resume)
    _update_batch_status(folder_job_id, "started")

    # ═══ 自动分批循环：每批 eff_batch 行，全部跑完自动结束 ═══
    current_start = start_row
    total_processed = 0
    final_has_more = False
    last_error = None

    try:
        while True:
            # 每批开始前检查取消信号
            signal = check_control()
            if signal == "cancel":
                raise _ControlExit("cancel", current_start)
            if signal == "pause":
                raise _ControlExit("pause", current_start)

            batch_info.clear()
            df = run_pipeline(
                inp, out,
                dry_run=dry_run, no_fetch=no_fetch,
                limit=eff_batch, start_row=current_start,
                append_output=(current_start > 0 or append_output),
                progress_callback=rq_progress,
                control_callback=check_control,
                batch_info_out=batch_info,
                row_save_callback=row_saver,
            )

            batch_end = batch_info.get("batch_end_exclusive", current_start + len(df))
            batch_rows = batch_end - current_start
            total_processed += batch_rows
            final_has_more = bool(batch_info.get("has_more", False))

            logger.info("RQ batch done: rows %d-%d, total_processed=%d, has_more=%s",
                        current_start + 1, batch_end, total_processed, final_has_more)

            if not final_has_more:
                break

            # 自动续跑下一批
            current_start = batch_end
            rq_progress({"phase": "batch_done", "current": total_processed,
                         "total": batch_info.get("total_rows", total_processed),
                         "message": f"已完成 {total_processed} 行，自动继续下一批..."})
            time.sleep(0.5)  # 给 Redis 一点时间同步

    except _ControlExit as e:
        logger.warning("RQ job %s %s at row %d", folder_job_id, e.reason, e.row)
        if e.reason == "cancel":
            _update_batch_status(folder_job_id, "cancelled")
        else:
            _update_batch_status(folder_job_id, "paused")
        _ctrl_conn.delete(f"job_control:{folder_job_id}")
        # 保存进度以便续跑
        prog_path = job_dir / "progress.json"
        n_total = batch_info.get("total_rows", total_processed)
        if n_total > 0 and e.row < n_total:
            json.dump({"total_rows": n_total, "next_start_row": e.row,
                       "batch_size": eff_batch, "has_more": True},
                      prog_path.open("w"), ensure_ascii=False, indent=2)
        return {"rows": max(0, e.row - start_row), "output_path": str(out),
                "control": e.reason, "batch_start_row": start_row,
                "batch_end_exclusive": e.row}
    except Exception:
        logger.exception("RQ job %s pipeline error", folder_job_id)
        _update_batch_status(folder_job_id, "failed")
        _ctrl_conn.delete(f"job_control:{folder_job_id}")
        raise

    # 全部完成
    n_total = batch_info.get("total_rows", total_processed)
    _update_batch_total(folder_job_id, inp.name, n_total)
    prog_path = job_dir / "progress.json"
    if prog_path.is_file():
        prog_path.unlink()
    _ctrl_conn.delete(f"job_control:{folder_job_id}")

    logger.info("RQ folder=%s ALL DONE: %s rows", folder_job_id, total_processed)
    return {"rows": total_processed, "output_path": str(out),
            "has_more": False, "total_rows": n_total}


def run_url_eval_job(
    folder_job_id: str,
    data_root: str,
    *,
    url: str,
    company_name: str = "",
    country: str = "US",
    target_products: str = "",
    notes: str = "",
    dry_run: bool = False,
    no_fetch: bool = False,
) -> dict[str, Any]:
    """URL 快速评估：构造单行 DataFrame → 走标准 pipeline → 入库。"""
    import pandas as pd

    root = Path(data_root)
    job_dir = root / "jobs" / folder_job_id
    out = job_dir / "output.xlsx"

    # 构造单行输入
    row = {
        "company_name": company_name,
        "website": url,
        "country_region": country,
        "target_products": target_products,
        "notes": notes,
        "contact_name": "", "contact_email": "", "contact_phone": "",
        "contact_address": "", "evidence_paste": "", "priority": "",
    }
    df_input = pd.DataFrame([row])

    # 保存为 csv 供 pipeline 读取
    inp = job_dir / "input.csv"
    df_input.to_csv(inp, index=False)

    logger.info("URL eval: url=%s company=%s", url, company_name)

    from rq import get_current_job
    from tools.pipeline.runner import run_pipeline

    batch_info: dict[str, Any] = {}

    try:
        df = run_pipeline(
            inp, out,
            dry_run=dry_run, no_fetch=no_fetch,
            limit=1, start_row=0, append_output=False,
            progress_callback=(lambda _: None),
            batch_info_out=batch_info,
        )
    except Exception:
        logger.exception("URL eval job %s pipeline error", folder_job_id)
        _update_batch_status(folder_job_id, "failed")
        raise

    n = len(df)
    _save_to_database(folder_job_id, f"URL: {url}", df)
    _update_batch_status(folder_job_id, "finished")

    logger.info("URL eval done: rows=%s url=%s", n, url)
    return {"rows": n, "output_path": str(out), "url": url}


def _update_batch_total(batch_id: str, filename: str, total_rows: int) -> None:
    from src.core.database import get_db
    db = get_db()
    db.execute(
        "INSERT INTO evaluation_batch (id, original_filename, total_rows, status) "
        "VALUES (?, ?, ?, 'finished') ON CONFLICT(id) DO UPDATE SET "
        "original_filename=excluded.original_filename, total_rows=excluded.total_rows, "
        "status='finished', completed_at=datetime('now','localtime')",
        (batch_id, filename, total_rows),
    )
    db.commit()
    db.execute("PRAGMA wal_checkpoint(FULL)")


def _update_batch_status(batch_id: str, status: str) -> None:
    from src.core.database import get_db
    db = get_db()
    db.execute(
        "INSERT INTO evaluation_batch (id, original_filename, total_rows, status) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET status=excluded.status",
        (batch_id, "", 0, status),
    )
    if status == "finished":
        db.execute(
            "UPDATE evaluation_batch SET completed_at=datetime('now','localtime') WHERE id=?",
            (batch_id,),
        )
    db.commit()


def _make_row_saver(batch_id: str, filename: str, resume: bool = False):
    """返回一个 row_save_callback 函数：每行评估完成后立即入库。
    若 resume=True，不删除已有行（断点续跑）。
    """
    from src.core.database import get_db
    from pandas import isna as pd_isna

    _init_done = False

    def _ensure_init():
        nonlocal _init_done
        if _init_done:
            return
        _init_done = True
        db = get_db()
        if not resume:
            db.execute("DELETE FROM customer WHERE batch_id=?", (batch_id,))
        db.execute(
            "INSERT INTO evaluation_batch (id, original_filename, total_rows, status) "
            "VALUES (?, ?, 0, 'started') ON CONFLICT(id) DO UPDATE SET "
            "original_filename=excluded.original_filename, status='started'",
            (batch_id, filename),
        )
        db.commit()
        db.execute("PRAGMA wal_checkpoint(FULL)")

    col_map = {
        "company_name": "company_name", "website": "website",
        "country_region": "country_region", "contact_name": "contact_name",
        "contact_email": "contact_email", "contact_phone": "contact_phone",
        "contact_address": "contact_address", "target_products": "target_products",
        "priority": "priority", "notes": "notes",
        "product_fit_score": "product_fit_score", "product_fit_reasons": "product_fit_reasons",
        "capability_score": "capability_score", "capability_signals": "capability_signals",
        "reputation_facts": "reputation_facts", "reputation_concerns": "reputation_concerns",
        "reputation_sources": "reputation_sources",
        "reputation_safety_score": "reputation_safety_score",
        "buyer_seller_role": "buyer_seller_role", "buyer_seller_reason": "buyer_seller_reason",
        "deal_recommendation": "deal_recommendation", "next_action": "next_action",
        "confidence": "confidence", "data_quality": "data_quality",
        "fetched_pages": "fetched_pages", "errors": "errors",
        "overall_score_computed": "overall_score_computed",
        "manual_review_flag": "manual_review_flag", "eval_json": "eval_json",
        "contact_emails_all": "contact_emails_all",
        "social_profiles": "social_profiles",
    }

    _saved_count = 0

    def save_row(row_idx: int, row_series, meta_info: dict):
        nonlocal _saved_count
        _ensure_init()
        db = get_db()
        row_values: list[Any] = [batch_id, int(row_idx)]
        for df_col, db_col in col_map.items():
            if df_col in row_series.index:
                val = row_series[df_col]
                if hasattr(val, "iloc") and hasattr(val, "shape"):
                    try:
                        if len(val) > 0:
                            val = val.iloc[0]
                        else:
                            val = None
                    except Exception:
                        val = None
                if hasattr(val, "item"):
                    try:
                        val = val.item()
                    except (ValueError, TypeError):
                        val = None
                if pd_isna(val):
                    val = None
                elif isinstance(val, str):
                    val = val.strip()
            else:
                val = None
            row_values.append(val)

        db_cols = ["batch_id", "row_index"] + list(col_map.values())
        placeholders = ", ".join("?" for _ in db_cols)
        sql = f"INSERT OR REPLACE INTO customer ({', '.join(db_cols)}) VALUES ({placeholders})"
        db.execute(sql, row_values)
        db.commit()

        # 每 10 行更新一次断点进度
        _saved_count += 1
        if _saved_count % 10 == 0:
            db.execute(
                "UPDATE evaluation_batch SET rows_completed=? WHERE id=?",
                (_saved_count, batch_id),
            )
            db.commit()
            db.execute("PRAGMA wal_checkpoint(FULL)")

    return save_row


def _save_to_database(batch_id: str, filename: str, df) -> None:
    from src.core.database import get_db
    from pandas import isna as pd_isna
    db = get_db()

    # Upsert batch record
    db.execute(
        "INSERT INTO evaluation_batch (id, original_filename, total_rows, status) "
        "VALUES (?, ?, ?, 'finished') ON CONFLICT(id) DO UPDATE SET "
        "original_filename=excluded.original_filename, total_rows=excluded.total_rows, "
        "status='finished', completed_at=datetime('now','localtime')",
        (batch_id, filename, len(df)),
    )

    # Map DataFrame columns to DB columns
    col_map = {
        "company_name": "company_name", "website": "website",
        "country_region": "country_region", "contact_name": "contact_name",
        "contact_email": "contact_email", "contact_phone": "contact_phone",
        "contact_address": "contact_address", "target_products": "target_products",
        "priority": "priority", "notes": "notes",
        "product_fit_score": "product_fit_score", "product_fit_reasons": "product_fit_reasons",
        "capability_score": "capability_score", "capability_signals": "capability_signals",
        "reputation_facts": "reputation_facts", "reputation_concerns": "reputation_concerns",
        "reputation_sources": "reputation_sources",
        "reputation_safety_score": "reputation_safety_score",
        "buyer_seller_role": "buyer_seller_role", "buyer_seller_reason": "buyer_seller_reason",
        "deal_recommendation": "deal_recommendation", "next_action": "next_action",
        "confidence": "confidence", "data_quality": "data_quality",
        "fetched_pages": "fetched_pages", "errors": "errors",
        "overall_score_computed": "overall_score_computed",
        "manual_review_flag": "manual_review_flag", "eval_json": "eval_json",
        "contact_emails_all": "contact_emails_all",
        "social_profiles": "social_profiles",
    }

    # Transaction: atomic delete + insert for this batch
    # Use SAVEPOINT to avoid "cannot start a transaction within a transaction"
    _saved = False
    try:
        db.execute("SAVEPOINT _save_batch")
        _saved = True
    except Exception:
        pass
    db.execute("DELETE FROM customer WHERE batch_id=?", (batch_id,))

    # Build fixed column order for executemany batch INSERT
    db_cols = ["batch_id", "row_index"] + list(col_map.values())
    columns_str = ", ".join(db_cols)
    placeholders_str = ", ".join("?" for _ in db_cols)
    sql = f"INSERT INTO customer ({columns_str}) VALUES ({placeholders_str})"

    values_list: list[tuple[Any, ...]] = []
    for idx, row in df.iterrows():
        row_values: list[Any] = [batch_id, int(idx)]
        for df_col, db_col in col_map.items():
            if df_col in df.columns:
                val = row[df_col]
                # Guard against pandas Series (duplicate column names)
                if hasattr(val, "iloc") and hasattr(val, "shape"):
                    try:
                        if len(val) > 0:
                            val = val.iloc[0]
                        else:
                            val = None
                    except Exception:
                        val = None
                if hasattr(val, "item"):
                    try:
                        val = val.item()
                    except ValueError:
                        val = None
                if pd_isna(val):
                    val = None
                elif isinstance(val, str):
                    val = val.strip()
                    # XSS prevention: only allow http/https URLs in website field
                    if db_col == "website" and val and not (val.lower().startswith("http://") or val.lower().startswith("https://")):
                        val = "https://" + val
            else:
                val = None
            row_values.append(val)
        values_list.append(tuple(row_values))

    db.executemany(sql, values_list)
    if _saved:
        try:
            db.execute("RELEASE _save_batch")
        except Exception as e:
            logger.warning("RELEASE SAVEPOINT _save_batch 失败 (可忽略): %s", e)
    db.commit()
    # 强制 WAL checkpoint，确保数据写入主数据库文件
    db.execute("PRAGMA wal_checkpoint(FULL)")
    logger.info("Saved %d customers to database for batch %s", len(df), batch_id)
