"""
公司内部网页：上传 Excel → Redis 队列异步执行 run_pipeline → 下载结果。

本地开发::
  终端1: redis-server
  终端2: rq worker -u redis://127.0.0.1:6379/0 eval_jobs
  终端3: uvicorn internal_web.main:app --reload --app-dir .
"""

from __future__ import annotations

import json
import logging
import shutil
import uuid
from pathlib import Path
from typing import Annotated, Any

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from redis import Redis
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job

from internal_web.config import InternalWebSettings, load_settings
from internal_web.tasks import run_eval_job

logger = logging.getLogger(__name__)

security = HTTPBasic(auto_error=False)

STATIC_DIR = Path(__file__).resolve().parent / "static"


def get_settings() -> InternalWebSettings:
    return load_settings()


def require_auth(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
    settings: Annotated[InternalWebSettings, Depends(get_settings)],
) -> None:
    if not settings.basic_user or not settings.basic_password:
        return
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="需要登录",
            headers={"WWW-Authenticate": "Basic"},
        )
    if credentials.username != settings.basic_user or credentials.password != settings.basic_password:
        raise HTTPException(
            status_code=401,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Basic"},
        )


def get_queue(settings: InternalWebSettings) -> Queue:
    conn = Redis.from_url(settings.redis_url)
    return Queue(settings.queue_name, connection=conn)


def _job_connection(settings: InternalWebSettings) -> Redis:
    return Redis.from_url(settings.redis_url)


def _folder_rq_job_id(settings: InternalWebSettings, folder_job_id: str) -> str:
    """Redis 中 RQ Job 的 id；继续下一批时会与目录 UUID 不同。"""
    p = settings.data_dir / "jobs" / folder_job_id / "rq_job_id.txt"
    if p.is_file():
        s = p.read_text(encoding="utf-8").strip()
        if s:
            return s
    return folder_job_id


app = FastAPI(title="客户评估（内部）", version="1.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index_page(
    _: Annotated[None, Depends(require_auth)],
) -> HTMLResponse:
    index_file = STATIC_DIR / "index.html"
    if not index_file.is_file():
        return HTMLResponse("<p>缺少 static/index.html</p>", status_code=500)
    return HTMLResponse(index_file.read_text(encoding="utf-8"))


@app.post("/api/jobs")
async def create_job(
    _: Annotated[None, Depends(require_auth)],
    settings: Annotated[InternalWebSettings, Depends(get_settings)],
    file: UploadFile = File(...),
    dry_run: bool = Form(False),
    no_fetch: bool = Form(False),
    limit: str | None = Form(None),
    batch_size: str | None = Form(None),
) -> JSONResponse:
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(400, "请上传 .xlsx 文件")

    job_id = str(uuid.uuid4())
    job_dir = settings.data_dir / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    dest = job_dir / "input.xlsx"

    size_limit = settings.max_upload_mb * 1024 * 1024
    written = 0
    try:
        with dest.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > size_limit:
                    shutil.rmtree(job_dir, ignore_errors=True)
                    raise HTTPException(413, f"文件超过限制（最大 {settings.max_upload_mb} MB）")
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(500, f"保存上传失败: {e}") from e

    try:
        df_probe = pd.read_excel(dest, engine="openpyxl")
        if len(df_probe) > settings.max_rows:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise HTTPException(
                413,
                f"行数超过限制（最多 {settings.max_rows} 行，当前 {len(df_probe)} 行）",
            )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        shutil.rmtree(job_dir, ignore_errors=True)
        raise HTTPException(400, f"无法读取 Excel: {e}") from e

    lim: int | None = None
    if limit and str(limit).strip():
        try:
            lim = max(1, int(limit))
        except ValueError:
            lim = None

    bs: int | None = None
    if batch_size and str(batch_size).strip():
        try:
            bs = max(1, int(batch_size))
        except ValueError:
            bs = None

    queue = get_queue(settings)
    rq_job = queue.enqueue(
        run_eval_job,
        job_id,
        str(settings.data_dir),
        dry_run=dry_run,
        no_fetch=no_fetch,
        limit=lim if bs is None else None,
        batch_size=bs,
        start_row=0,
        append_output=False,
        job_id=job_id,
        job_timeout=60 * 45,
        failure_ttl=86400,
        result_ttl=86400,
    )
    (job_dir / "rq_job_id.txt").write_text(rq_job.id, encoding="utf-8")
    logger.info(
        "任务已入队 job_id=%s dry_run=%s no_fetch=%s limit=%s batch_size=%s",
        job_id,
        dry_run,
        no_fetch,
        lim,
        bs,
    )
    return JSONResponse({"job_id": job_id, "rq_job_id": rq_job.id, "status": "queued"})


@app.post("/api/jobs/{job_id}/continue")
def continue_job(
    job_id: str,
    _: Annotated[None, Depends(require_auth)],
    settings: Annotated[InternalWebSettings, Depends(get_settings)],
    dry_run: bool = Form(False),
    no_fetch: bool = Form(False),
) -> JSONResponse:
    """同一上传目录下继续处理下一批（依赖 progress.json）。"""
    job_dir = settings.data_dir / "jobs" / job_id
    prog_path = job_dir / "progress.json"
    if not prog_path.is_file():
        raise HTTPException(409, "没有可继续的进度（可能已一次跑完或未使用分批）")
    try:
        prog = json.loads(prog_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"progress.json 损坏: {e}") from e
    if not prog.get("has_more"):
        raise HTTPException(409, "已全部处理完毕")
    start = int(prog["next_start_row"])
    bs = int(prog["batch_size"]) if prog.get("batch_size") is not None else None
    if bs is None or bs < 1:
        raise HTTPException(400, "progress.json 缺少有效的 batch_size")

    queue = get_queue(settings)
    rq_job = queue.enqueue(
        run_eval_job,
        job_id,
        str(settings.data_dir),
        dry_run=dry_run,
        no_fetch=no_fetch,
        limit=None,
        batch_size=bs,
        start_row=start,
        append_output=True,
        job_timeout=60 * 45,
        failure_ttl=86400,
        result_ttl=86400,
    )
    (job_dir / "rq_job_id.txt").write_text(rq_job.id, encoding="utf-8")
    logger.info("继续分批入队 folder=%s rq=%s start_row=%s batch_size=%s", job_id, rq_job.id, start, bs)
    return JSONResponse({"job_id": job_id, "rq_job_id": rq_job.id, "status": "queued"})


@app.get("/api/jobs/{job_id}")
def get_job_status(
    job_id: str,
    _: Annotated[None, Depends(require_auth)],
    settings: Annotated[InternalWebSettings, Depends(get_settings)],
) -> dict[str, Any]:
    conn = _job_connection(settings)
    rq_id = _folder_rq_job_id(settings, job_id)
    try:
        job = Job.fetch(rq_id, connection=conn)
    except NoSuchJobError:
        raise HTTPException(404, "任务不存在") from None

    st = job.get_status()
    # RQ 枚举 str() 为 "JobStatus.FAILED"，前端需稳定小写串（与枚举 .value 一致）
    status_str = st.value if hasattr(st, "value") else str(st)
    body: dict[str, Any] = {"job_id": job_id, "status": status_str}
    meta = getattr(job, "meta", None) or {}
    if isinstance(meta, dict) and meta.get("progress"):
        body["progress"] = meta["progress"]
    if job.is_finished:
        body["result"] = job.result
    elif job.is_failed:
        body["error"] = job.exc_info or "任务失败"
    return body


@app.get("/api/jobs/{job_id}/download")
def download_result(
    job_id: str,
    _: Annotated[None, Depends(require_auth)],
    settings: Annotated[InternalWebSettings, Depends(get_settings)],
) -> FileResponse:
    conn = _job_connection(settings)
    rq_id = _folder_rq_job_id(settings, job_id)
    try:
        job = Job.fetch(rq_id, connection=conn)
    except NoSuchJobError:
        raise HTTPException(404, "任务不存在") from None

    if not job.is_finished:
        raise HTTPException(409, "任务未完成或失败")

    out = settings.data_dir / "jobs" / job_id / "output.xlsx"
    if not out.is_file():
        raise HTTPException(404, "结果文件不存在")

    return FileResponse(
        path=out,
        filename=f"customer_eval_{job_id}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
