"""CLI 批量导入工具 — 将文档目录/文件导入知识库。

用法:
    python tools/import_kb.py ./docs/产品资料/ --collection 产品信息
    python tools/import_kb.py ./产品手册.pdf --collection 产品信息
    python tools/import_kb.py ./docs/ --collection 公司文档 --incremental
    python tools/import_kb.py ./docs/ --dry-run

每个文件一个 RQ job，支持并发处理（默认 2 个 worker 并行）。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("import_kb")

# 目标知识库数据目录
KB_DATA_DIR = REPO_ROOT / "var" / "knowledge_base"


def discover_files(root: str | Path) -> list[Path]:
    """递归发现目录中所有可导入文件。"""
    root = Path(root)
    if root.is_file():
        return [root]

    from tools.doc_parser import SUPPORTED_EXTS
    files: list[Path] = []
    for ext in SUPPORTED_EXTS:
        files.extend(root.rglob(f"*{ext}"))
        files.extend(root.rglob(f"*{ext.upper()}"))
    return sorted(set(files))


def process_single(
    file_path: Path,
    collection: str,
    *,
    enable_ocr_cleanup: bool = True,
) -> dict:
    """处理单个文件并入库（非 RQ 模式，直接调用）。"""
    from tools.doc_parser import process_file
    from tools.vector_store import add_documents

    results = process_file(file_path, collection=collection, enable_ocr_cleanup=enable_ocr_cleanup)
    if not results:
        return {"file": file_path.name, "parents": 0, "children": 0, "status": "empty"}

    n = add_documents(collection, results)
    return {
        "file": file_path.name,
        "parents": len(results),
        "children": n,
        "status": "ok",
    }


def process_single_rq(
    file_path: str,
    collection: str,
    *,
    redis_url: str = "",
    enable_ocr_cleanup: bool = True,
) -> str:
    """通过 RQ 队列异步处理单个文件。返回 job_id。"""
    from redis import Redis
    from rq import Queue

    conn = Redis.from_url(redis_url or os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0"))
    queue = Queue("knowledge_base:default", connection=conn)

    job_id = str(uuid.uuid4())
    queue.enqueue(
        _process_file_job,
        job_id,
        file_path,
        collection,
        enable_ocr_cleanup,
        job_id=job_id,
        job_timeout=1800,
        failure_ttl=86400,
        result_ttl=86400,
    )
    return job_id


def _process_file_job(
    job_id: str,
    file_path: str,
    collection: str,
    enable_ocr_cleanup: bool = True,
) -> dict:
    """RQ worker 执行的文档处理任务（入口函数，便于 rq worker 导入）。"""
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env", override=False)

    return process_single(
        Path(file_path),
        collection,
        enable_ocr_cleanup=enable_ocr_cleanup,
    )


def save_manifest(result: dict[str, Any], output_dir: Path) -> None:
    """保存导入清单到 job output 目录。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "import_manifest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description="知识库批量导入工具")
    parser.add_argument("path", help="文件或目录路径")
    parser.add_argument("--collection", "-c", default="产品信息",
                        choices=["产品信息", "公司文档", "采购表单"],
                        help="目标 collection（默认: 产品信息）")
    parser.add_argument("--redis-url", default="",
                        help="Redis URL（默认: 读取 REDIS_URL 环境变量）")
    parser.add_argument("--async", dest="use_rq", action="store_true",
                        help="使用 RQ 队列异步处理")
    parser.add_argument("--dry-run", action="store_true",
                        help="预览模式：只显示分块方案，不入库")
    parser.add_argument("--incremental", action="store_true",
                        help="增量模式：跳过已导入的文件")
    parser.add_argument("--no-ocr-cleanup", action="store_true",
                        help="禁用 OCR 后 DeepSeek 整理")
    parser.add_argument("--copy-raw", action="store_true",
                        help="将原始文件复制到 var/knowledge_base/raw_docs/")
    args = parser.parse_args()

    # 发现文件
    files = discover_files(args.path)
    if not files:
        logger.error("未发现任何可导入文件: %s", args.path)
        sys.exit(1)

    logger.info("发现 %d 个文件", len(files))
    for f in files:
        logger.info("  - %s (%s)", f.name, _fmt_size(f))

    if args.dry_run:
        logger.info("=== DRY-RUN 模式，仅预览分块 ===")
        from tools.doc_parser import process_file
        total_p = total_c = 0
        for f in files:
            results = process_file(f, collection=args.collection, enable_ocr_cleanup=not args.no_ocr_cleanup)
            np = len(results)
            nc = sum(len(r["children"]) for r in results)
            total_p += np
            total_c += nc
            logger.info("  %s → %d 父文档, %d 子文档", f.name, np, nc)
        logger.info("合计: %d 父文档, %d 子文档", total_p, total_c)
        sys.exit(0)

    # 增量检查
    if args.incremental:
        from tools.vector_store import list_documents
        existing = {d["source_file"] for d in list_documents(args.collection)}
        files = [f for f in files if f.name not in existing]
        if not files:
            logger.info("所有文件已导入，无需处理")
            sys.exit(0)
        logger.info("增量模式: %d 个新文件待导入", len(files))

    # 处理
    manifest = {"collection": args.collection, "files": [], "total_parents": 0, "total_children": 0}
    if args.use_rq:
        redis_url = args.redis_url or os.environ.get("REDIS_URL", "redis://127.0.0.1:6379/0")
        logger.info("使用 RQ 异步模式 (Redis: %s)", redis_url)
        for f in files:
            job_id = process_single_rq(str(f), args.collection, redis_url=redis_url,
                                       enable_ocr_cleanup=not args.no_ocr_cleanup)
            manifest["files"].append({"file": f.name, "job_id": job_id, "status": "queued"})
            logger.info("  入队: %s (job=%s)", f.name, job_id[:8])
        logger.info("所有文件已入队。启动 worker: rq worker -u %s knowledge_base:default --worker-class rq.SimpleWorker", redis_url)
    else:
        # 同步处理
        from tqdm import tqdm  # type: ignore
        for f in tqdm(files, desc="导入进度"):
            r = process_single(f, args.collection, enable_ocr_cleanup=not args.no_ocr_cleanup)
            manifest["files"].append(r)
            manifest["total_parents"] += r.get("parents", 0)
            manifest["total_children"] += r.get("children", 0)
            if args.copy_raw:
                dest = KB_DATA_DIR / "raw_docs" / args.collection / f.name
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, dest)

        # 输出统计
        logger.info("=" * 50)
        logger.info("导入完成!")
        logger.info("  文件数: %d", len(files))
        logger.info("  父文档: %d", manifest["total_parents"])
        logger.info("  子文档: %d", manifest["total_children"])
        logger.info("=" * 50)

    save_manifest(manifest, KB_DATA_DIR / "job_outputs")


def _fmt_size(path: Path) -> str:
    size = path.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.0f}{unit}"
        size /= 1024
    return f"{size:.0f}TB"


if __name__ == "__main__":
    main()
