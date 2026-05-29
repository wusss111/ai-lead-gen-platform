"""Customer eval API routes."""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from rq.exceptions import NoSuchJobError
from rq.job import Job

from src.core.auth import require_admin
from src.core.config import PlatformConfig, get_config
from src.core.redis_utils import get_queue, get_rq_job_info
from src.agents.customer_eval.config import CustomerEvalConfig
from src.agents.customer_eval.tasks import run_eval_job, run_url_eval_job
from src.core.database import get_db, dicts_from_rows

logger = logging.getLogger(__name__)

router = APIRouter(tags=["customer-eval"])

_TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _job_connection(config: PlatformConfig) -> Any:
    from redis import Redis
    return Redis.from_url(config.redis_url)


def _folder_rq_job_id(config: PlatformConfig, folder_job_id: str) -> str:
    p = config.data_dir / "jobs" / folder_job_id / "rq_job_id.txt"
    if p.is_file():
        s = p.read_text(encoding="utf-8").strip()
        if s:
            return s
    return folder_job_id


# ---- Page routes ----

@router.get("/", response_class=HTMLResponse)
def eval_page(
    request: Request,
    _: Annotated[None, Depends(require_admin)],
):
    from src.core.app import app
    t = app.state.jinja_env.get_template("eval_index.html")
    return HTMLResponse(t.render({
        "request": request,
        "nav_agents": app.state.nav_agents,
        "active_agent": "customer-eval",
    }))


# ---- API routes ----

@router.post("/api/jobs")
async def create_job(
    _: Annotated[None, Depends(require_admin)],
    config: Annotated[PlatformConfig, Depends(get_config)],
    file: UploadFile = File(...),
    dry_run: bool = Form(False),
    no_fetch: bool = Form(False),
    limit: str | None = Form(None),
    batch_size: str | None = Form(None),
) -> JSONResponse:
    eval_cfg = CustomerEvalConfig.from_env()

    if not file.filename:
        raise HTTPException(400, "请上传文件")

    fname = file.filename.lower()
    is_csv = fname.endswith(".csv")
    is_xlsx = fname.endswith(".xlsx")

    if not is_csv and not is_xlsx:
        raise HTTPException(400, "请上传 .xlsx 或 .csv 文件")

    job_id = str(uuid.uuid4())
    job_dir = config.data_dir / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    ext = ".csv" if is_csv else ".xlsx"
    dest = job_dir / f"input{ext}"

    size_limit = eval_cfg.max_upload_mb * 1024 * 1024
    written = 0
    try:
        with dest.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > size_limit:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    raise HTTPException(413, f"文件超过大小限制 ({eval_cfg.max_upload_mb} MB)")
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(500, f"文件保存失败: {e}") from e

    # 读取验证
    try:
        if is_csv:
            from tools.pipeline.io_excel import _detect_csv_encoding, _detect_csv_separator
            encoding = _detect_csv_encoding(dest)
            sep = _detect_csv_separator(dest, encoding)
            try:
                df_probe = pd.read_csv(dest, encoding=encoding, sep=sep, dtype=str)
            except Exception:
                df_probe = pd.read_csv(dest, encoding=encoding, sep=None, engine="python", dtype=str)
        else:
            df_probe = pd.read_excel(dest, engine="openpyxl")
        if len(df_probe) > eval_cfg.max_rows:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(413, f"行数超过限制 ({eval_cfg.max_rows})")
    except HTTPException:
        raise
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(400, f"无法读取文件: {e}") from e

    lim = _parse_int(limit)
    bs = _parse_int(batch_size)

    queue = get_queue(config.redis_url, eval_cfg.queue_name)
    rq_job = queue.enqueue(
        run_eval_job,
        job_id,
        str(config.data_dir),
        dry_run=dry_run,
        no_fetch=no_fetch,
        limit=lim if bs is None else None,
        batch_size=bs,
        start_row=0,
        append_output=False,
        input_ext=ext,
        job_id=job_id,
        job_timeout=eval_cfg.job_timeout,
        failure_ttl=eval_cfg.result_ttl,
        result_ttl=eval_cfg.result_ttl,
    )
    (job_dir / "rq_job_id.txt").write_text(rq_job.id, encoding="utf-8")

    # Create pending batch record
    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO evaluation_batch (id, original_filename, total_rows, status) VALUES (?,?,?,?)",
        (job_id, file.filename, len(df_probe), "queued"),
    )
    db.commit()

    logger.info("Job enqueued job_id=%s rq_id=%s dry_run=%s format=%s", job_id, rq_job.id, dry_run, ext)
    return JSONResponse({"job_id": job_id, "rq_job_id": rq_job.id, "status": "queued", "format": ext, "total_rows": len(df_probe)})


@router.post("/api/jobs/{job_id}/continue")
def continue_job(
    job_id: str,
    _: Annotated[None, Depends(require_admin)],
    config: Annotated[PlatformConfig, Depends(get_config)],
    dry_run: bool = Form(False),
    no_fetch: bool = Form(False),
) -> JSONResponse:
    eval_cfg = CustomerEvalConfig.from_env()
    job_dir = config.data_dir / "jobs" / job_id
    prog_path = job_dir / "progress.json"
    if not prog_path.is_file():
        raise HTTPException(409, "No progress to continue (batch may have finished)")
    try:
        prog = json.loads(prog_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(400, f"progress.json corrupt: {e}") from e
    if not prog.get("has_more"):
        raise HTTPException(409, "All rows already processed")
    start = int(prog["next_start_row"])
    bs = int(prog.get("batch_size") or 300)

    queue = get_queue(config.redis_url, eval_cfg.queue_name)
    rq_job = queue.enqueue(
        run_eval_job,
        job_id, str(config.data_dir),
        dry_run=dry_run, no_fetch=no_fetch,
        limit=None, batch_size=bs, start_row=start, append_output=True,
        job_timeout=eval_cfg.job_timeout,
        failure_ttl=eval_cfg.result_ttl,
        result_ttl=eval_cfg.result_ttl,
    )
    (job_dir / "rq_job_id.txt").write_text(rq_job.id, encoding="utf-8")
    logger.info("Continue batch folder=%s rq=%s start=%s", job_id, rq_job.id, start)
    return JSONResponse({"job_id": job_id, "rq_job_id": rq_job.id, "status": "queued"})


@router.get("/api/batches")
def list_eval_batches(
    user: Annotated[dict, Depends(require_admin)],
    config: Annotated[PlatformConfig, Depends(get_config)],
) -> JSONResponse:
    from src.core.database import get_db, dicts_from_rows
    db = get_db()
    rows = dicts_from_rows(
        db.execute("SELECT * FROM evaluation_batch ORDER BY created_at DESC LIMIT 20").fetchall()
    )
    # 注入 RQ 真实状态，前端区分"运行中"和"真中断"
    for r in rows:
        rq_id_file = config.data_dir / "jobs" / r["id"] / "rq_job_id.txt"
        info = get_rq_job_info(rq_id_file, config.redis_url)
        r["rq_status"] = info.get("rq_status", "unknown")
    return JSONResponse(rows)


@router.get("/api/jobs/{job_id}")
def get_job_status(
    job_id: str,
    _: Annotated[None, Depends(require_admin)],
    config: Annotated[PlatformConfig, Depends(get_config)],
) -> dict[str, Any]:
    rq_id_file = config.data_dir / "jobs" / job_id / "rq_job_id.txt"
    info = get_rq_job_info(rq_id_file, config.redis_url)

    if info["rq_status"] in ("unknown", "not_found"):
        raise HTTPException(404, "Job not found") from None

    body: dict[str, Any] = {"job_id": job_id, "status": info["rq_status"]}
    if info.get("progress"):
        body["progress"] = info["progress"]

    # 读取 Redis 中持久化的 next_job_id（rq_job_id.txt 被覆盖后仍可续跑）
    try:
        from redis import Redis as _R
        _red = _R.from_url(config.redis_url)
        next_raw = _red.get(f"job_next:{job_id}")
        if next_raw:
            next_data = json.loads(next_raw if isinstance(next_raw, str) else next_raw.decode("utf-8"))
            body["next_job_id"] = next_data.get("next_job_id")
            body["batch_rows"] = next_data.get("batch_rows")
            body["total_rows"] = next_data.get("total_rows")
            body["batch_end_exclusive"] = next_data.get("batch_end")
            if not body.get("progress"):
                body["progress"] = {}
            body["progress"]["next_job_id"] = next_data.get("next_job_id")
            body["progress"]["batch_rows"] = next_data.get("batch_rows")
    except Exception:
        logger.warning("Failed to read job_next from Redis for %s", job_id, exc_info=True)

    # 从 DB 获取 total_rows，让前端提前显示批次信息（如 "批次 1/17"）
    from src.core.database import get_db
    db = get_db()
    batch_row = db.execute(
        "SELECT total_rows, rows_completed FROM evaluation_batch WHERE id=?", (job_id,)
    ).fetchone()
    if batch_row and batch_row["total_rows"]:
        body["total_rows"] = batch_row["total_rows"]
    # 注入 rows_completed 作为前端的 _totalAccumulated 回退值（即使 Redis job_next 过期也能正确显示批次号）
    if batch_row and batch_row["rows_completed"]:
        body["rows_completed"] = batch_row["rows_completed"]

    # Fetch Job object for result/error details
    rq_id = _folder_rq_job_id(config, job_id)
    conn = _job_connection(config)
    try:
        job = Job.fetch(rq_id, connection=conn)
    except NoSuchJobError:
        raise HTTPException(404, "Job not found") from None

    if job.is_finished:
        body["result"] = job.result or {}
        if body["result"].get("next_job_id"):
            body["next_job_id"] = body["result"]["next_job_id"]
    elif job.is_failed:
        body["error"] = job.exc_info or "Job failed"
    return body


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(
    job_id: str,
    _: Annotated[None, Depends(require_admin)],
    config: Annotated[PlatformConfig, Depends(get_config)],
) -> JSONResponse:
    """Cancel a running evaluation job. Saves partial results before stopping."""
    from redis import Redis as _Redis
    from src.core.database import get_db
    conn = _Redis.from_url(config.redis_url)
    conn.setex(f"job_control:{job_id}", 600, "cancel")
    # 兜底：如果 Worker 已死，直接更新 DB 状态
    db = get_db()
    db.execute(
        "UPDATE evaluation_batch SET status='cancelled' WHERE id=? AND status='started'",
        (job_id,),
    )
    db.commit()
    logger.info("Cancel signal sent for job %s", job_id)
    return JSONResponse({"status": "ok", "message": "取消信号已发送，正在保存已处理数据..."})


@router.post("/api/jobs/{job_id}/pause")
def pause_job(
    job_id: str,
    _: Annotated[None, Depends(require_admin)],
    config: Annotated[PlatformConfig, Depends(get_config)],
) -> JSONResponse:
    """Pause a running evaluation job. Saves progress for later resume."""
    from redis import Redis as _Redis
    conn = _Redis.from_url(config.redis_url)
    conn.setex(f"job_control:{job_id}", 600, "pause")
    logger.info("Pause signal sent for job %s", job_id)
    return JSONResponse({"status": "ok", "message": "暂停信号已发送，正在保存进度..."})


@router.post("/api/jobs/{job_id}/resume")
def resume_job(
    job_id: str,
    _admin: Annotated[None, Depends(require_admin)],
    config: Annotated[PlatformConfig, Depends(get_config)],
) -> JSONResponse:
    """恢复中断的评估任务（从断点继续）。"""
    from src.core.database import get_db
    db = get_db()
    batch = db.execute(
        "SELECT * FROM evaluation_batch WHERE id=? AND status='started'", (job_id,)
    ).fetchone()
    if not batch:
        raise HTTPException(404, "该任务不是可恢复状态")

    eval_cfg = CustomerEvalConfig.from_env()
    job_dir = config.data_dir / "jobs" / job_id

    # 检查是否已有活跃的 RQ job，避免重复入队
    rq_id_file = job_dir / "rq_job_id.txt"
    info = get_rq_job_info(rq_id_file, config.redis_url)
    if info["rq_status"] in ("queued", "started"):
        raise HTTPException(409, "任务已在运行中，无需重复恢复")

    # Detect input format
    inp_csv = job_dir / "input.csv"
    inp_xlsx = job_dir / "input.xlsx"
    input_ext = ".csv" if inp_csv.is_file() else ".xlsx"

    queue = get_queue(config.redis_url, eval_cfg.queue_name)
    rq_job = queue.enqueue(
        run_eval_job,
        job_id,
        str(config.data_dir),
        dry_run=False,
        no_fetch=False,
        start_row=batch["rows_completed"] or 0,
        batch_size=None,
        append_output=True,
        input_ext=input_ext,
        job_timeout=eval_cfg.job_timeout,
        failure_ttl=eval_cfg.result_ttl,
        result_ttl=eval_cfg.result_ttl,
    )
    (job_dir / "rq_job_id.txt").write_text(rq_job.id, encoding="utf-8")
    logger.info("Resumed job_id=%s rq_id=%s from row %s", job_id, rq_job.id, batch["rows_completed"])
    return JSONResponse({"status": "ok", "job_id": job_id, "rq_job_id": rq_job.id, "resume_from": batch["rows_completed"]})


@router.get("/api/jobs/{job_id}/download")
def download_result(
    job_id: str,
    _: Annotated[None, Depends(require_admin)],
    config: Annotated[PlatformConfig, Depends(get_config)],
) -> FileResponse:
    conn = _job_connection(config)
    rq_id = _folder_rq_job_id(config, job_id)
    try:
        job = Job.fetch(rq_id, connection=conn)
    except NoSuchJobError:
        raise HTTPException(404, "Job not found") from None

    if not job.is_finished:
        raise HTTPException(409, "Job not finished or failed")

    out = config.data_dir / "jobs" / job_id / "output.xlsx"
    if not out.is_file():
        raise HTTPException(404, "Result file not found")

    return FileResponse(
        path=out,
        filename=f"customer_eval_{job_id}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/api/url-eval")
def url_eval(
    _: Annotated[None, Depends(require_admin)],
    config: Annotated[PlatformConfig, Depends(get_config)],
    url: str = Form(...),
    company_name: str = Form(""),
    country: str = Form(""),
    target_products: str = Form(""),
    notes: str = Form(""),
    dry_run: bool = Form(False),
    no_fetch: bool = Form(False),
) -> JSONResponse:
    """手动输入网站 URL 进行快速评估。"""
    eval_cfg = CustomerEvalConfig.from_env()

    url = url.strip()
    if not url:
        raise HTTPException(400, "请输入网站 URL")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    job_id = str(uuid.uuid4())
    job_dir = config.data_dir / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    queue = get_queue(config.redis_url, eval_cfg.queue_name)
    rq_job = queue.enqueue(
        run_url_eval_job,
        job_id,
        str(config.data_dir),
        url=url,
        company_name=company_name.strip(),
        country=country.strip() if country.strip() else "US",
        target_products=target_products.strip(),
        notes=notes.strip(),
        dry_run=dry_run,
        no_fetch=no_fetch,
        job_id=job_id,
        job_timeout=eval_cfg.job_timeout,
        failure_ttl=eval_cfg.result_ttl,
        result_ttl=eval_cfg.result_ttl,
    )
    (job_dir / "rq_job_id.txt").write_text(rq_job.id, encoding="utf-8")

    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO evaluation_batch (id, original_filename, total_rows, status) VALUES (?,?,?,?)",
        (job_id, f"URL: {url}", 1, "queued"),
    )
    db.commit()

    logger.info("URL eval enqueued job_id=%s rq_id=%s url=%s", job_id, rq_job.id, url)
    return JSONResponse({"job_id": job_id, "rq_job_id": rq_job.id, "status": "queued"})


def _parse_int(val: str | None) -> int | None:
    if val and str(val).strip():
        try:
            return max(1, int(val))
        except ValueError:
            return None
    return None
