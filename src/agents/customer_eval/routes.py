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

from src.core.auth import require_auth
from src.core.config import PlatformConfig, get_config
from src.core.redis_utils import get_queue, get_rq_job_info
from src.agents.customer_eval.config import CustomerEvalConfig
from src.agents.customer_eval.tasks import run_eval_job
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
def eval_page(request: Request):
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
    _: Annotated[None, Depends(require_auth)],
    config: Annotated[PlatformConfig, Depends(get_config)],
    file: UploadFile = File(...),
    dry_run: bool = Form(False),
    no_fetch: bool = Form(False),
    limit: str | None = Form(None),
    batch_size: str | None = Form(None),
) -> JSONResponse:
    eval_cfg = CustomerEvalConfig.from_env()

    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "Please upload a .xlsx file")

    job_id = str(uuid.uuid4())
    job_dir = config.data_dir / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    dest = job_dir / "input.xlsx"

    size_limit = eval_cfg.max_upload_mb * 1024 * 1024
    written = 0
    try:
        with dest.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > size_limit:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    raise HTTPException(413, f"File exceeds limit ({eval_cfg.max_upload_mb} MB)")
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(500, f"Failed to save upload: {e}") from e

    try:
        df_probe = pd.read_excel(dest, engine="openpyxl")
        if len(df_probe) > eval_cfg.max_rows:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(413, f"Row count exceeds limit ({eval_cfg.max_rows})")
    except HTTPException:
        raise
    except Exception as e:
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(400, f"Cannot read Excel: {e}") from e

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

    logger.info("Job enqueued job_id=%s rq_id=%s dry_run=%s", job_id, rq_job.id, dry_run)
    return JSONResponse({"job_id": job_id, "rq_job_id": rq_job.id, "status": "queued"})


@router.post("/api/jobs/{job_id}/continue")
def continue_job(
    job_id: str,
    _: Annotated[None, Depends(require_auth)],
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
    bs = int(prog.get("batch_size") or 0)
    if bs < 1:
        raise HTTPException(400, "progress.json missing valid batch_size")

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


@router.get("/api/jobs/{job_id}")
def get_job_status(
    job_id: str,
    _: Annotated[None, Depends(require_auth)],
    config: Annotated[PlatformConfig, Depends(get_config)],
) -> dict[str, Any]:
    rq_id_file = config.data_dir / "jobs" / job_id / "rq_job_id.txt"
    info = get_rq_job_info(rq_id_file, config.redis_url)

    if info["rq_status"] in ("unknown", "not_found"):
        raise HTTPException(404, "Job not found") from None

    body: dict[str, Any] = {"job_id": job_id, "status": info["rq_status"]}
    if info.get("progress"):
        body["progress"] = info["progress"]

    # Fetch Job object for result/error details
    rq_id = _folder_rq_job_id(config, job_id)
    conn = _job_connection(config)
    try:
        job = Job.fetch(rq_id, connection=conn)
    except NoSuchJobError:
        raise HTTPException(404, "Job not found") from None

    if job.is_finished:
        body["result"] = job.result
    elif job.is_failed:
        body["error"] = job.exc_info or "Job failed"
    return body


@router.get("/api/jobs/{job_id}/download")
def download_result(
    job_id: str,
    _: Annotated[None, Depends(require_auth)],
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


def _parse_int(val: str | None) -> int | None:
    if val and str(val).strip():
        try:
            return max(1, int(val))
        except ValueError:
            return None
    return None
