"""Redis connection pooling and RQ queue helpers."""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Any

from redis import Redis
from rq import Queue

logger = logging.getLogger(__name__)


@functools.lru_cache(maxsize=8)
def _get_redis(url: str) -> Redis:
    return Redis.from_url(url)


def get_queue(redis_url: str, queue_name: str) -> Queue:
    conn = _get_redis(redis_url)
    return Queue(queue_name, connection=conn)


def get_rq_job_info(job_id_file_path: Path | str, redis_url: str) -> dict[str, Any]:
    """Read an RQ job ID from a file, fetch the Job from Redis, return status info.

    Returns dict with ``rq_status`` and optionally ``progress``.
    Handles errors gracefully: missing file / NoSuchJobError / connection
    failures all return ``{"rq_status": "unknown"}`` or ``{"rq_status": "not_found"}``.
    """
    from rq.job import Job
    from rq.exceptions import NoSuchJobError

    path = Path(job_id_file_path)
    try:
        if not path.is_file():
            return {"rq_status": "unknown"}
        raw = path.read_text(encoding="utf-8-sig")
        rq_id = raw.strip().lstrip("﻿")
        if not rq_id:
            return {"rq_status": "unknown"}
        conn = _get_redis(redis_url)
        job = Job.fetch(rq_id, connection=conn)
        st = job.get_status()
        status_str = st.value if hasattr(st, "value") else str(st)
        result: dict[str, Any] = {"rq_status": status_str}
        meta = getattr(job, "meta", None) or {}
        if isinstance(meta, dict) and meta.get("progress"):
            result["progress"] = meta["progress"]
        return result
    except NoSuchJobError:
        return {"rq_status": "not_found"}
    except Exception:
        logger.debug("Could not fetch RQ job info from %s", path, exc_info=True)
        return {"rq_status": "unknown"}
