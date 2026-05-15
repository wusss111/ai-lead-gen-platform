"""RQ Worker 执行入口（需在仓库根目录 PYTHONPATH 下运行）。"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 进度写入 Redis 过于频繁时，在部分环境下会加重负载；行级进度最多约每 1.2s 写一次
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
    """执行客户评估流水线，结果写入 ``{data_root}/jobs/{folder_job_id}/output.xlsx``。"""
    root = Path(data_root)
    job_dir = root / "jobs" / folder_job_id
    inp = job_dir / "input.xlsx"
    out = job_dir / "output.xlsx"
    if not inp.is_file():
        raise FileNotFoundError(f"输入文件不存在: {inp}")

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
        except Exception:  # noqa: BLE001
            logger.debug("job.save_meta 跳过", exc_info=True)

    batch_info: dict[str, Any] = {}
    logger.info(
        "RQ folder=%s start: input=%s start_row=%s limit=%s append=%s",
        folder_job_id,
        inp,
        start_row,
        eff_limit,
        append_output,
    )
    try:
        df = run_pipeline(
            inp,
            out,
            dry_run=dry_run,
            no_fetch=no_fetch,
            limit=eff_limit,
            start_row=start_row,
            append_output=append_output,
            progress_callback=rq_progress,
            batch_info_out=batch_info,
        )
    except Exception:
        logger.exception("RQ job %s 流水线异常（完整堆栈见上）", folder_job_id)
        raise
    n = len(df)
    prog_path = job_dir / "progress.json"
    bs = batch_size if batch_size is not None else eff_limit
    if batch_info.get("has_more") and bs is not None:
        prog_path.write_text(
            json.dumps(
                {
                    "total_rows": batch_info.get("total_rows", n),
                    "next_start_row": batch_info.get("batch_end_exclusive", n),
                    "batch_size": bs,
                    "has_more": True,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    else:
        if prog_path.is_file():
            prog_path.unlink()

    logger.info("RQ folder=%s done: rows=%s out=%s", folder_job_id, n, out)
    return {
        "rows": n,
        "output_path": str(out),
        "batch_start_row": batch_info.get("batch_start_row", 0),
        "batch_end_exclusive": batch_info.get("batch_end_exclusive", n),
        "has_more": bool(batch_info.get("has_more", False)),
        "total_rows": batch_info.get("total_rows", n),
    }
