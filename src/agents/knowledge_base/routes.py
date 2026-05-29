# -*- coding: utf-8 -*-
"""Knowledge base API and page routes."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.core.auth import require_auth
from src.core.config import PlatformConfig, get_config

logger = logging.getLogger(__name__)

router = APIRouter(tags=["knowledge-base"])

from tools.pipeline.paths import REPO_ROOT as _REPO_ROOT

# 预设知识库分类
PRESET_COLLECTIONS = ["产品信息", "公司文档", "采购表单"]


# -- Page route --


@router.get("/", response_class=HTMLResponse)
def kb_page(request: Request):
    from src.core.app import app
    t = app.state.jinja_env.get_template("kb_index.html")
    return HTMLResponse(t.render({
        "request": request,
        "nav_agents": app.state.nav_agents,
        "active_agent": "knowledge-base",
        "collections": PRESET_COLLECTIONS,
    }))


# -- Collection APIs --


@router.get("/api/kb/collections")
def list_collections(
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    """列出所有知识库 collection 及统计."""
    from tools.vector_store import get_collections, get_collection_stats

    result = []
    for name in PRESET_COLLECTIONS:
        stats = get_collection_stats(name)
        result.append({"name": name, **stats})

    return JSONResponse(result)


# -- Document APIs --


@router.get("/api/kb/documents")
def list_documents(
    _: Annotated[None, Depends(require_auth)],
    collection: str = "",
) -> JSONResponse:
    """列出文档列表.collection 为空时列出所有."""
    from tools.vector_store import list_documents

    if collection:
        docs = list_documents(collection)
    else:
        docs = []
        for c in PRESET_COLLECTIONS:
            docs.extend(list_documents(c))

    return JSONResponse(docs)


@router.delete("/api/kb/documents/{source_file:path}")
def delete_document(
    source_file: str,
    _: Annotated[None, Depends(require_auth)],
    collection: str = "",
) -> JSONResponse:
    """删除文档及其所有 chunk."""
    from tools.vector_store import delete_document

    if not collection:
        # 在所有 collection 中尝试删除
        total = 0
        for c in PRESET_COLLECTIONS:
            total += delete_document(c, source_file)
    else:
        total = delete_document(collection, source_file)

    return JSONResponse({"status": "ok", "deleted": total})


@router.get("/api/kb/doc/{source_file:path}/preview")
def preview_document(
    source_file: str,
    _: Annotated[None, Depends(require_auth)],
    collection: str = "产品信息",
) -> JSONResponse:
    """获取文档的原始文本预览(取第一个父文档的前 2000 字)."""
    from tools.vector_store import list_documents, get_parent_chunk, _get_client, _get_or_create, _safe_name

    client = _get_client()
    try:
        safe_coll = _safe_name(collection)
        parent_coll = _get_or_create(client, f"{safe_coll}_parents")
        data = parent_coll.get(where={"source_file": source_file})
        if data["ids"]:
            first_id = data["ids"][0]
            # 合并所有父文档
            all_text = "\n\n---\n\n".join(data["documents"][:5])
            return JSONResponse({
                "source_file": source_file,
                "preview": all_text[:5000],
                "parent_count": len(data["ids"]),
            })
    except Exception:
        pass

    return JSONResponse({"source_file": source_file, "preview": "", "parent_count": 0})


# -- Import APIs --


@router.post("/api/kb/import")
def import_directory(
    _: Annotated[None, Depends(require_auth)],
    config: Annotated[PlatformConfig, Depends(get_config)],
    directory: str = Form(""),
    collection: str = Form("产品信息"),
    ocr_cleanup: str = Form("1"),
) -> JSONResponse:
    """从目录路径导入文档,每个文件一个 RQ job."""
    from pathlib import Path
    from src.core.redis_utils import get_queue
    from src.agents.knowledge_base.tasks import process_file_job
    from tools.doc_parser import SUPPORTED_EXTS

    dir_path = Path(directory.strip())
    if not dir_path.exists():
        return JSONResponse({"status": "error", "error": f"目录不存在: {directory}"}, status_code=400)

    # 发现文件
    files: list[Path] = []
    if dir_path.is_file():
        files = [dir_path]
    else:
        for ext in SUPPORTED_EXTS:
            files.extend(dir_path.rglob(f"*{ext}"))
            files.extend(dir_path.rglob(f"*{ext.upper()}"))
        files = sorted(set(files))

    if not files:
        return JSONResponse({"status": "error", "error": "未发现支持的文档文件"}, status_code=400)

    enable_ocr = ocr_cleanup.strip() not in ("0", "false", "no")

    queue = get_queue(config.redis_url, "knowledge_base:default")
    jobs = []
    for fp in files:
        job_id = str(uuid.uuid4())
        file_path = str(fp.resolve())
        rq_job = queue.enqueue(
            process_file_job,
            job_id,
            file_path,
            collection,
            enable_ocr,
            job_id=job_id,
            job_timeout=1800,
            failure_ttl=86400,
            result_ttl=86400,
        )
        jobs.append({"file": fp.name, "job_id": job_id, "rq_job_id": rq_job.id})

    return JSONResponse({
        "status": "queued",
        "collection": collection,
        "file_count": len(jobs),
        "jobs": jobs,
        "message": f"已入队 {len(jobs)} 个文件,请启动 worker: rq worker -u {config.redis_url} knowledge_base:default --worker-class rq.SimpleWorker",
    })


@router.post("/api/kb/ingest-text")
def ingest_text(
    _: Annotated[None, Depends(require_auth)],
    config: Annotated[PlatformConfig, Depends(get_config)],
    text: str = Form(""),
    title: str = Form(""),
    collection: str = Form("产品信息"),
) -> JSONResponse:
    """文本粘贴入库."""
    if not text.strip():
        return JSONResponse({"status": "error", "error": "文本内容为空"}, status_code=400)

    import sys
    tools_dir = str(_REPO_ROOT)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)

    from tools.doc_parser import split_into_parents, parent_to_children, extract_metadata
    from tools.vector_store import add_documents

    parents = split_into_parents(text, source_type="text")
    docs = []
    for p in parents:
        children = parent_to_children(p["text"])
        meta = extract_metadata(p, title or "粘贴文本")
        meta["collection"] = collection
        meta["source_file"] = title or "粘贴文本"
        docs.append({
            "parent_text": p["text"],
            "children": children,
            "metadata": meta,
        })

    n = add_documents(collection, docs)
    return JSONResponse({
        "status": "ok",
        "title": title or "粘贴文本",
        "parents": len(docs),
        "children": n,
    })


# -- Search API --


@router.get("/api/kb/search")
def search_knowledge(
    _: Annotated[None, Depends(require_auth)],
    query: str = "",
    collection: str = "",
    mode: str = "hybrid_rerank",
    top_k: int = 5,
) -> JSONResponse:
    """检索知识库.支持三种模式:vector / hybrid / hybrid_rerank"""
    from tools.vector_store import search, search_multi

    if not query.strip():
        return JSONResponse({"results": [], "query": ""})

    try:
        if collection:
            results = search(collection, query.strip(), top_k=top_k, mode=mode)
        else:
            results = search_multi(PRESET_COLLECTIONS, query.strip(), top_k=top_k, mode=mode)
    except RuntimeError as e:
        return JSONResponse({"error": str(e), "results": [], "query": query}, status_code=503)
    except Exception as e:
        logger.exception("搜索失败")
        return JSONResponse({"error": f"搜索失败: {e}", "results": [], "query": query}, status_code=500)

    # 清理元数据中不可序列化的字段
    for r in results:
        if "metadata" in r:
            r["metadata"] = {k: str(v) if not isinstance(v, (str, int, float, bool, type(None))) else v
                             for k, v in r["metadata"].items()}

    return JSONResponse({
        "query": query,
        "collection": collection or "all",
        "mode": mode,
        "results": results,
        "count": len(results),
    })


# -- Job status --


@router.get("/api/kb/jobs/{job_id}")
def get_job_status(
    job_id: str,
    _: Annotated[None, Depends(require_auth)],
    config: Annotated[PlatformConfig, Depends(get_config)],
) -> JSONResponse:
    """查询文档处理 job 状态."""
    from src.core.redis_utils import get_rq_job_info
    from rq.job import Job
    from redis import Redis

    # RQ job 信息
    info = get_rq_job_info(job_id, config.redis_url)

    result = None
    if info["rq_status"] == "finished":
        try:
            conn = Redis.from_url(config.redis_url)
            job = Job.fetch(job_id, connection=conn)
            result = job.result
        except Exception:
            pass

    return JSONResponse({
        "job_id": job_id,
        "status": info["rq_status"],
        "progress": info.get("progress"),
        "result": result,
    })
