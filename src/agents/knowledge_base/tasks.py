"""RQ background tasks for knowledge base document processing.

Worker 进程必须加载 .env：已在函数内调用 load_dotenv()。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

from tools.pipeline.paths import REPO_ROOT as _REPO_ROOT


def process_file_job(
    job_id: str,
    file_path: str,
    collection: str,
    enable_ocr_cleanup: bool = True,
) -> dict:
    """RQ worker 执行的文档处理任务。

    完整流水线：解析 → OCR整理 → 分块 → 嵌入 → 入库。
    """
    from dotenv import load_dotenv
    load_dotenv(_REPO_ROOT / ".env", override=False)

    # 确保 tools/ 在 sys.path
    tools_dir = str(_REPO_ROOT)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)

    from tools.doc_parser import process_file
    from tools.vector_store import add_documents

    fp = Path(file_path)
    if not fp.is_file():
        return {"status": "error", "error": f"文件不存在: {file_path}"}

    logger.info("处理文档: %s → collection=%s", fp.name, collection)

    try:
        results = process_file(
            fp,
            collection=collection,
            enable_ocr_cleanup=enable_ocr_cleanup,
        )
        if not results:
            return {"status": "empty", "file": fp.name}

        n = add_documents(collection, results)

        # 复制原始文件到 raw_docs/
        from tools.vector_store import DEFAULT_PERSIST_DIR
        raw_dir = DEFAULT_PERSIST_DIR.parent / "raw_docs" / collection
        raw_dir.mkdir(parents=True, exist_ok=True)
        import shutil
        shutil.copy2(fp, raw_dir / fp.name)

        return {
            "status": "ok",
            "file": fp.name,
            "collection": collection,
            "parents": len(results),
            "children": n,
        }
    except Exception as e:
        logger.exception("处理文档失败: %s", fp.name)
        return {"status": "error", "file": fp.name, "error": str(e)}


def process_text_job(
    job_id: str,
    text: str,
    title: str,
    collection: str,
) -> dict:
    """处理纯文本入库（无需文件解析）。"""
    from dotenv import load_dotenv
    load_dotenv(_REPO_ROOT / ".env", override=False)

    tools_dir = str(_REPO_ROOT)
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)

    from tools.doc_parser import split_into_parents, parent_to_children, extract_metadata
    from tools.vector_store import add_documents

    logger.info("处理文本: %s → collection=%s", title, collection)

    try:
        parents = split_into_parents(text, source_type="text")
        docs = []
        for p in parents:
            children = parent_to_children(p["text"])
            meta = extract_metadata(p, title)
            meta["collection"] = collection
            meta["source_file"] = title
            docs.append({
                "parent_text": p["text"],
                "children": children,
                "metadata": meta,
            })

        n = add_documents(collection, docs)
        return {"status": "ok", "title": title, "parents": len(docs), "children": n}
    except Exception as e:
        logger.exception("处理文本失败: %s", title)
        return {"status": "error", "title": title, "error": str(e)}
