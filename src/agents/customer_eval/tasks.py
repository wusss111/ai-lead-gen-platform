"""RQ Worker task: run customer evaluation pipeline, save results to SQLite."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

# Ensure .env is loaded for worker processes
from dotenv import load_dotenv as _load_dotenv
_env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
if _env_path.is_file():
    _load_dotenv(_env_path)

logger = logging.getLogger(__name__)

_PROGRESS_ROW_MIN_INTERVAL_S = 1.2


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
) -> dict[str, Any]:
    """Run eval pipeline, write output.xlsx, and save results to SQLite."""
    root = Path(data_root)
    job_dir = root / "jobs" / folder_job_id
    inp = job_dir / "input.xlsx"
    out = job_dir / "output.xlsx"
    if not inp.is_file():
        raise FileNotFoundError(f"Input file not found: {inp}")

    if start_row > 0:
        append_output = True

    if batch_size is not None:
        eff_limit = max(1, int(batch_size))
    elif limit is not None:
        eff_limit = max(1, int(limit))
    else:
        eff_limit = None

    from rq import get_current_job

    from tools.pipeline.runner import run_pipeline

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

    batch_info: dict[str, Any] = {}
    logger.info(
        "RQ folder=%s start: input=%s start_row=%s limit=%s append=%s",
        folder_job_id, inp, start_row, eff_limit, append_output,
    )

    try:
        df = run_pipeline(
            inp, out,
            dry_run=dry_run, no_fetch=no_fetch,
            limit=eff_limit, start_row=start_row,
            append_output=append_output,
            progress_callback=rq_progress,
            batch_info_out=batch_info,
        )
    except Exception:
        logger.exception("RQ job %s pipeline error", folder_job_id)
        _update_batch_status(folder_job_id, "failed")
        raise

    n = len(df)
    _save_to_database(folder_job_id, inp.name, df)

    prog_path = job_dir / "progress.json"
    bs = batch_size if batch_size is not None else eff_limit
    if batch_info.get("has_more") and bs is not None:
        prog_path.write_text(
            json.dumps({
                "total_rows": batch_info.get("total_rows", n),
                "next_start_row": batch_info.get("batch_end_exclusive", n),
                "batch_size": bs,
                "has_more": True,
            }, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    else:
        if prog_path.is_file():
            prog_path.unlink()
        _update_batch_status(folder_job_id, "finished")

    logger.info("RQ folder=%s done: rows=%s out=%s", folder_job_id, n, out)
    return {
        "rows": n,
        "output_path": str(out),
        "batch_start_row": batch_info.get("batch_start_row", 0),
        "batch_end_exclusive": batch_info.get("batch_end_exclusive", n),
        "has_more": bool(batch_info.get("has_more", False)),
        "total_rows": batch_info.get("total_rows", n),
    }


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
    }

    # Transaction: atomic delete + insert for this batch
    db.execute("BEGIN")
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
                if hasattr(val, "item"):
                    val = val.item()
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
    db.commit()
    logger.info("Saved %d customers to database for batch %s", len(df), batch_id)
