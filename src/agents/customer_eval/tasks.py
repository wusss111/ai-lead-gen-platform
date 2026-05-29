"""RQ Worker task: 客户评估管道 — 子进程架构。

主进程负责：读 Excel → 预抓取网站（httpx）→ 启动子进程 AI 评估 → 收进度。
子进程隔离内存，退出后 OS 强制回收，彻底解决 pymalloc 累积问题。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd

from src.core.database import get_db

# 确保 Worker 进程能读到 .env
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


# ═══════════════════════════════════════════════════════════════════════
# 子进程入口
# ═══════════════════════════════════════════════════════════════════════

def _child_entry(args_path: str) -> None:
    """multiprocessing.Process target：子进程入口。

    通过独立的 Python 子进程运行 worker_child.run_child_batch()。
    子进程退出后 OS 强制回收全部内存。
    """
    import sys
    sys_path_root = str(Path(__file__).resolve().parent.parent.parent.parent)
    if sys_path_root not in sys.path:
        sys.path.insert(0, sys_path_root)

    from src.agents.customer_eval.worker_child import run_child_batch
    done = run_child_batch(args_path)
    sys.exit(0 if done > 0 else 1)


# ═══════════════════════════════════════════════════════════════════════
# 主进程辅助函数
# ═══════════════════════════════════════════════════════════════════════

def _report(job, payload: dict) -> None:
    """安全地保存进度到 RQ job meta（主线程专用）。"""
    if job is None:
        return
    try:
        job.meta["progress"] = payload
        job.save_meta()
    except Exception:
        pass


def _save_progress(job_dir: Path, total: int, next_row: int, batch_size: int) -> None:
    """保存断点续跑进度。"""
    (job_dir / "progress.json").write_text(
        json.dumps({"total_rows": total, "next_start_row": next_row,
                    "batch_size": batch_size, "has_more": True},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ═══════════════════════════════════════════════════════════════════════
# 主 RQ Job 入口
# ═══════════════════════════════════════════════════════════════════════

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
    """主进程入口：读 Excel → 预抓取网站 → 子进程 AI 评估 → 自动入队下一批。

    每批：
      1. 主进程预抓取本批唯一网站（httpx, 3线程）
      2. 启动子进程（multiprocessing.Process）
      3. 子进程逐行评估+入库，结束后 OS 回收内存
      4. 如果还有剩余行，自动入队下一批 RQ Job
    """
    import gc as _gc
    import subprocess
    import sys

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
        eff_batch = 100

    from rq import get_current_job
    from tools.pipeline.runner import _prefetch_all_websites, _ControlExit
    from tools.pipeline.io_excel import load_excel_io
    from tools.pipeline.config_merge import merge_meta_with_file, merge_meta_from_env

    # 在主线程捕获 RQ job 引用（子线程/子进程不能调 get_current_job）
    _rq_job = get_current_job()
    _update_batch_status(folder_job_id, "started")

    # Redis 控制信号
    from redis import Redis as _Redis
    _redis_url = os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
    _ctrl_conn = _Redis.from_url(_redis_url)

    def check_control() -> str | None:
        try:
            val = _ctrl_conn.get(f"job_control:{folder_job_id}")
            if val:
                return val.decode("utf-8") if isinstance(val, bytes) else val
        except Exception:
            pass
        return None

    # ── 读 Excel + 预处理 ──
    meta = load_excel_io()
    meta = merge_meta_with_file(meta, None)
    meta = merge_meta_from_env(meta)

    if inp.suffix.lower() == ".csv":
        from tools.pipeline.io_excel import read_input_csv
        df, missing = read_input_csv(inp, meta=meta)
    else:
        from tools.pipeline.io_excel import read_input_xlsx
        df, missing = read_input_xlsx(inp, meta=meta)
    if missing:
        raise ValueError(f"输入表缺少必填列: {', '.join(missing)}")

    from tools.pipeline.io_excel import ensure_output_columns
    df = ensure_output_columns(df, meta)

    n_total = len(df)
    _update_batch_total(folder_job_id, inp.name, n_total)

    # ── 当前批次处理 ──
    current_start = start_row
    total_processed = 0

    try:
        signal = check_control()
        if signal == "cancel":
            _update_batch_status(folder_job_id, "cancelled")
            _ctrl_conn.delete(f"job_control:{folder_job_id}")
            return {"rows": 0, "control": "cancel"}
        if signal == "pause":
            _save_progress(job_dir, n_total, current_start, eff_batch)
            _update_batch_status(folder_job_id, "paused")
            return {"rows": 0, "control": "pause"}

        batch_end = min(n_total, current_start + eff_batch)
        n_batch = batch_end - current_start

        # Phase 1: 预抓取本批网站（主进程，纯 httpx）
        _report(_rq_job, {"phase": "prefetch", "current": 0, "total": 1,
                          "message": "正在并行抓取网站..."})

        if not no_fetch:
            def _on_prefetch(done: int, total: int) -> None:
                _report(_rq_job, {"phase": "prefetch", "current": done, "total": total,
                                  "message": f"网站抓取 {done}/{total}"})

            from tools.pipeline.paths import resolve_cache_dir, resolve_catalog_path, resolve_kb_path
            _cache = resolve_cache_dir(None)
            _cat = resolve_catalog_path(None)
            _kb = resolve_kb_path(None)
            try:
                prefetch_cache = _prefetch_all_websites(
                    df, current_start, batch_end,
                    no_fetch=False, cache_dir=_cache,
                    control_callback=check_control,
                    progress_callback=_on_prefetch,
                )
            except _ControlExit as e:
                _update_batch_status(folder_job_id, "cancelled")
                _ctrl_conn.delete(f"job_control:{folder_job_id}")
                return {"rows": max(0, e.row - current_start), "control": e.reason}
        else:
            prefetch_cache = {}

        ok_count = sum(1 for _, errs in prefetch_cache.values() if not errs)
        _report(_rq_job, {"phase": "prefetch", "current": 1, "total": 1,
                          "message": f"网站抓取完成: {ok_count}/{len(prefetch_cache)}"})

        # 准备子进程参数（JSON 序列化，避免 pickle 大数据）
        df_slice = df.iloc[current_start:batch_end].copy()
        df_slice_json = df_slice.to_json(orient="split", force_ascii=False)

        # 预抓取缓存瘦身（去掉 html 字段减少传输量）
        cache_slim: dict[str, dict] = {}
        for ws, (pages, errs) in prefetch_cache.items():
            slim_pages = [{k: v for k, v in p.items() if k != "html"} for p in pages]
            cache_slim[ws] = {"pages": slim_pages, "errors": errs}

        args = {
            "df_slice_json": df_slice_json,
            "prefetch_cache": cache_slim,
            "meta_json": json.dumps(meta, ensure_ascii=False),
            "data_root": str(root),
            "batch_id": folder_job_id,
            "start_row": current_start,
            "catalog_path": str(_cat) if _cat.is_file() else "",
            "kb_path": str(_kb) if _kb.is_file() else "",
            "cache_dir": str(_cache),
            "skip_playwright": False,
        }
        args_path = job_dir / "child_args.json"
        args_path.write_text(json.dumps(args, ensure_ascii=False), encoding="utf-8")

        # 释放父进程大数据（prefetch_cache 含所有网站文本，子进程启动后不再需要）
        del prefetch_cache, cache_slim, df_slice, df_slice_json, args
        _gc.collect()

        # Phase 2: 子进程 AI 评估（subprocess.run，可靠性远胜 multiprocessing.spawn）
        _report(_rq_job, {"phase": "eval", "current": 0, "total": n_batch,
                          "message": f"AI 评估 0/{n_batch} 行"})

        child_ok = False
        for retry in range(2):
            proc = subprocess.Popen(
                [sys.executable, "-m", "src.agents.customer_eval.worker_child",
                 str(args_path)],
                cwd=str(Path(__file__).resolve().parent.parent.parent.parent),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            # 双线程读取子进程 stdout + stderr（Windows 管道满会导致子进程死锁）
            _child_lines = []
            _stderr_lines = []
            def _read_stdout():
                for _line in proc.stdout:
                    _child_lines.append(_line)
            def _read_stderr():
                for _line in proc.stderr:
                    _stderr_lines.append(_line)

            import threading as _th
            _reader = _th.Thread(target=_read_stdout, daemon=True)
            _reader.start()
            _stderr_reader = _th.Thread(target=_read_stderr, daemon=True)
            _stderr_reader.start()

            # 轮询等待，同时手动维持 RQ 心跳
            deadline = time.monotonic() + 3600
            _last_heartbeat = 0.0
            _last_child_progress = time.monotonic()  # 看门狗：子进程最后产出进度的时间
            _child_stuck_timeout = 600  # 10 分钟无进度 → 超时杀掉
            while proc.poll() is None:
                now = time.monotonic()
                if now > deadline:
                    proc.kill()
                    break

                # 每 30 秒手动刷新 RQ 心跳（Windows SimpleWorker 无心跳线程）
                if now - _last_heartbeat > 30 and _rq_job:
                    try:
                        from datetime import datetime, timezone
                        _rq_job.heartbeat(datetime.now(timezone.utc), 300)
                        _last_heartbeat = now
                    except Exception:
                        pass

                # 处理积攒的进度行
                while _child_lines:
                    line = _child_lines.pop(0)
                    _last_child_progress = time.monotonic()  # 收到进度 → 重置看门狗
                    try:
                        data = json.loads(line.decode("utf-8", errors="replace"))
                        if data.get("type") == "row":
                            _done = data["done"]
                            _tot = data["total"]
                            _name = data.get("name", "")
                            _report(_rq_job, {
                                "phase": "eval",
                                "current": _done,
                                "total": _tot,
                                "label": _name,
                                "message": f"AI 评估 {_done}/{_tot} " + chr(183) + f" {_name}",
                            })
                    except Exception:
                        pass

                # 检查控制信号
                signal = check_control()
                if signal in ("cancel", "pause"):
                    proc.kill()
                    proc.wait()
                    _reader.join(timeout=5)
                    _stderr_reader.join(timeout=5)
                    _update_batch_status(folder_job_id, "cancelled" if signal == "cancel" else "paused")
                    _ctrl_conn.delete(f"job_control:{folder_job_id}")
                    return {"rows": total_processed, "control": signal}

                # 看门狗：子进程超过 10 分钟无任何进度 → 超时杀掉（防止 API 僵死导致死等）
                if now - _last_child_progress > _child_stuck_timeout:
                    logger.error("子进程 %d 分钟无进度，超时杀掉", _child_stuck_timeout // 60)
                    proc.kill()
                    break

                time.sleep(0.5)
            _reader.join(timeout=5)
            _stderr_reader.join(timeout=5)
            try:
                stdout, stderr = proc.communicate(timeout=10)
            except Exception:
                stdout, stderr = b"", b""
            if proc.returncode == 0:
                child_ok = True
                break
            logger.warning("子进程退出码 %d，重试 %d/2", proc.returncode, retry + 1)
            args["skip_playwright"] = True
            args_path.write_text(json.dumps(args, ensure_ascii=False), encoding="utf-8")

        if not child_ok:
            stderr_lines = [_l.decode("utf-8", errors="replace") for _l in _stderr_lines]
            stderr_tail = "".join(stderr_lines[-20:]) or "无 stderr 输出"
            raise RuntimeError(f"子进程 2 次重试均失败。stderr:\n{stderr_tail}")

        total_processed = n_batch

        # 设置累进 rows_completed（本批结束后已处理到的行号）
        _pg_db = get_db()
        _pg_db.execute(
            "UPDATE evaluation_batch SET rows_completed=? WHERE id=?",
            (batch_end, folder_job_id),
        )
        _pg_db.commit()

        # Phase 3: 写 output.xlsx（主进程）
        _report(_rq_job, {"phase": "write", "current": n_batch, "total": n_batch,
                          "message": "正在写入 Excel…"})

        from tools.pipeline.io_excel import build_summary_export_df, write_result_xlsx
        batch_df = df.iloc[current_start:batch_end].copy()
        source_rows = list(range(current_start + 1, batch_end + 1))
        summary_part = build_summary_export_df(
            batch_df.reset_index(drop=True), meta, source_row_1based=source_rows,
        )

        if append_output and out.is_file():
            try:
                old = pd.read_excel(out, sheet_name="Summary", engine="openpyxl")
                summary_df = pd.concat([old, summary_part], ignore_index=True, sort=False)
            except Exception:
                summary_df = summary_part
        else:
            summary_df = summary_part

        write_result_xlsx(summary_df, out, detail_df=None, highlight_manual_review=True)
        logger.info("output.xlsx written: %s, rows=%d", out, len(summary_df))

        # 释放内存
        del df_slice, prefetch_cache, cache_slim, summary_part, summary_df
        _gc.collect()

        _report(_rq_job, {"phase": "batch_done", "current": n_batch, "total": n_batch,
                          "message": f"本批完成 {n_batch} 行"})

        # 如果还有下一批 → 自动入队
        has_more = batch_end < n_total
        if has_more:
            try:
                from rq import Queue as _RQQueue
                _q = _RQQueue("customer_eval:default", connection=_ctrl_conn)
                next_job = _q.enqueue(
                    "src.agents.customer_eval.tasks.run_eval_job",
                    folder_job_id, str(root),
                    dry_run=dry_run, no_fetch=no_fetch,
                    batch_size=eff_batch, start_row=batch_end,
                    append_output=True, input_ext=input_ext,
                    job_timeout=14400,
                )
                # 保存进度（确保 progress.json 反映最新断点）
                _save_progress(job_dir, n_total, batch_end, eff_batch)
                # 存 next_job_id 到 Redis（rq_job_id.txt 马上会被覆盖，Redis key 不受影响）
                _ctrl_conn.setex(
                    f"job_next:{folder_job_id}", 86400,
                    json.dumps({"next_job_id": next_job.id, "batch_rows": n_batch,
                                "batch_end": batch_end, "total_rows": n_total}),
                )
                (job_dir / "rq_job_id.txt").write_text(next_job.id, encoding="utf-8")
                _ctrl_conn.delete(f"job_control:{folder_job_id}")
                logger.info("Auto-enqueue next batch: start=%d next_job=%s", batch_end, next_job.id)
                return {"rows": total_processed, "total_rows": n_total,
                        "has_more": True, "next_job_id": next_job.id,
                        "batch_start_row": current_start, "batch_end_exclusive": batch_end}
            except Exception:
                logger.exception("批量入队下一批失败，保存断点")
                _save_progress(job_dir, n_total, batch_end, eff_batch)
                _update_batch_status(folder_job_id, "paused")
                _ctrl_conn.delete(f"job_control:{folder_job_id}")
                return {"rows": total_processed, "total_rows": n_total,
                        "error": True, "paused": True, "batch_end_exclusive": batch_end}

        # 全部完成
        _update_batch_total(folder_job_id, inp.name, n_total)
        _update_batch_status(folder_job_id, "finished")
        prog_path = job_dir / "progress.json"
        if prog_path.is_file():
            prog_path.unlink()
        _ctrl_conn.delete(f"job_control:{folder_job_id}")
        logger.info("RQ folder=%s ALL DONE: %s rows", folder_job_id, total_processed)
        return {"rows": total_processed, "total_rows": n_total, "has_more": False}

    except Exception:
        logger.exception("RQ job %s pipeline error", folder_job_id)
        # 保存断点进度（不覆盖已有 progress.json，防止内层已保存的正确值被覆盖）
        _prog_path = job_dir / "progress.json"
        if not _prog_path.is_file():
            try:
                _save_progress(job_dir, n_total, batch_end, eff_batch)
            except Exception:
                pass
        _update_batch_status(folder_job_id, "paused")
        _ctrl_conn.delete(f"job_control:{folder_job_id}")
        return {"rows": total_processed, "total_rows": n_total, "error": True,
                "paused": True, "batch_end_exclusive": batch_end}


# ═══════════════════════════════════════════════════════════════════════
# URL 快速评估
# ═══════════════════════════════════════════════════════════════════════

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
    root = Path(data_root)
    job_dir = root / "jobs" / folder_job_id
    out = job_dir / "output.xlsx"

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

    inp = job_dir / "input.csv"
    df_input.to_csv(inp, index=False)

    logger.info("URL eval: url=%s company=%s", url, company_name)

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


# ═══════════════════════════════════════════════════════════════════════
# 数据库辅助函数
# ═══════════════════════════════════════════════════════════════════════

def _update_batch_total(batch_id: str, filename: str, total_rows: int) -> None:
    db = get_db()
    db.execute(
        "INSERT INTO evaluation_batch (id, original_filename, total_rows, status) "
        "VALUES (?, ?, ?, 'started') ON CONFLICT(id) DO UPDATE SET "
        "original_filename=excluded.original_filename, total_rows=excluded.total_rows",
        (batch_id, filename, total_rows),
    )
    db.commit()
    db.execute("PRAGMA wal_checkpoint(FULL)")


def _update_batch_status(batch_id: str, status: str) -> None:
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
    """Save evaluation results to SQLite customer table（批量模式，用于 URL eval）。"""
    from pandas import isna as pd_isna
    db = get_db()

    db.execute("DELETE FROM customer WHERE batch_id=?", (batch_id,))

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
        "extracted_social": "extracted_social",
        "search_fallback_used": "search_fallback_used",
    }

    for i, (_, row) in enumerate(df.iterrows()):
        vals = [batch_id, i + 1]
        for df_col, db_col in col_map.items():
            v = row.get(df_col)
            try:
                if pd_isna(v):
                    v = None
            except (TypeError, ValueError):
                pass
            if isinstance(v, str):
                v = v.strip()
            vals.append(v)

        placeholders = ",".join("?" for _ in range(len(vals)))
        sql = f"INSERT INTO customer (batch_id, row_index, {','.join(col_map.values())}) VALUES ({placeholders})"
        db.execute(sql, vals)

    db.commit()
    _update_batch_status(batch_id, "finished")
