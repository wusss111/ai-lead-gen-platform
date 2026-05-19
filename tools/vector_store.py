"""向量存储 + 混合检索 + 重排序。

基于 ChromaDB + BM25（rank_bm25），实现：
- 父子文档存储：子文档用于检索，父文档（完整上下文）返回给 LLM
- 混合检索：BM25 关键词 + BGE-M3 语义 → RRF 融合
- Query 改写：DeepSeek 把口语化问题改写为多条检索 query
- 重排序：DeepSeek 对候选结果逐条打分，取 top-K
- 元数据过滤：section / language / date 等精确筛选
"""

from __future__ import annotations

import functools
import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ChromaDB 持久化目录（默认）
DEFAULT_PERSIST_DIR = Path(__file__).resolve().parent.parent / "var" / "knowledge_base" / "chroma_db"

# 中文名 → ASCII 安全名映射（ChromaDB 不支持中文 collection 名）
_NAME_MAP = {
    "产品信息": "kb_products",
    "公司文档": "kb_company_docs",
    "采购表单": "kb_procurement",
}
_REVERSE_NAME_MAP = {v: k for k, v in _NAME_MAP.items()}


def _safe_name(name: str) -> str:
    """将中文名转为 ASCII 安全的 ChromaDB collection 名。"""
    return _NAME_MAP.get(name, name)


def _display_name(safe: str) -> str:
    """将 ASCII 安全名转回中文显示名。"""
    return _REVERSE_NAME_MAP.get(safe, safe)


# BM25 索引（进程内缓存，{collection_name: BM25Okapi}}
_bm25_indices: dict[str, Any] = {}
_bm25_corpora: dict[str, list[str]] = {}        # {collection_name: tokenized_texts}
_bm25_doc_ids: dict[str, list[str]] = {}        # {collection_name: [chunk_id, ...]}
_bm25_lock = threading.Lock()


# -- ChromaDB 客户端 --


@functools.lru_cache(maxsize=1)
def _get_client(persist_dir: str = ""):
    """获取 ChromaDB PersistentClient（单例缓存）。"""
    import chromadb
    d = str(persist_dir or DEFAULT_PERSIST_DIR)
    Path(d).mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=d)


def _get_or_create(client, name: str) -> Any:
    """获取或创建 ChromaDB collection。"""
    try:
        return client.get_collection(name)
    except Exception:
        return client.create_collection(name, metadata={"hnsw:space": "cosine"})


# -- 存储 --


def add_documents(
    collection_name: str,
    documents: list[dict],
    *,
    persist_dir: str = "",
) -> int:
    """批量入库结构化文档（来自 doc_parser.process_file 的输出）。

    每个 document: {"parent_text": "...", "children": ["...","..."], "metadata": {...}}

    父文档存入 {collection_name}_parents，子文档存入 {collection_name}。
    返回入库的子文档总数。
    """
    from tools.embedding import get_embeddings

    safe = _safe_name(collection_name)
    client = _get_client(persist_dir)
    parent_coll = _get_or_create(client, f"{safe}_parents")
    child_coll = _get_or_create(client, safe)

    total_children = 0
    parent_batch: dict[str, list] = {"ids": [], "documents": [], "metadatas": []}
    child_batch: dict[str, list] = {"ids": [], "documents": [], "metadatas": [], "embeddings": []}

    for i, doc in enumerate(documents):
        parent_id = doc["metadata"].get("source_file", "doc") + f"_p{i}"
        meta = doc.get("metadata", {})

        # 父文档
        parent_batch["ids"].append(parent_id)
        parent_batch["documents"].append(doc["parent_text"])
        parent_batch["metadatas"].append({**meta, "chunk_index": i})

        # 子文档
        children = doc.get("children", [doc["parent_text"]])
        for j, child_text in enumerate(children):
            child_id = f"{parent_id}_c{j}"
            child_batch["ids"].append(child_id)
            child_batch["documents"].append(child_text)
            child_batch["metadatas"].append({**meta, "parent_id": parent_id, "child_index": j})
            total_children += 1

    if parent_batch["ids"]:
        parent_coll.add(**parent_batch)

    if child_batch["ids"]:
        # 批量嵌入
        embeddings = get_embeddings(child_batch["documents"])
        child_batch["embeddings"] = embeddings
        child_coll.add(**child_batch)

    # 重建 BM25 索引
    _rebuild_bm25(collection_name, persist_dir)

    logger.info("入库完成: collection=%s, 父文档=%d, 子文档=%d",
                 collection_name, len(parent_batch["ids"]), total_children)
    return total_children


def delete_document(
    collection_name: str,
    source_file: str,
    *,
    persist_dir: str = "",
) -> int:
    """按源文件名删除文档及其所有 chunk。返回删除的 chunk 总数。"""
    safe = _safe_name(collection_name)
    client = _get_client(persist_dir)
    child_coll = _get_or_create(client, safe)
    parent_coll = _get_or_create(client, f"{safe}_parents")

    # 找到所有匹配的子文档
    results = child_coll.get(where={"source_file": source_file})
    if results["ids"]:
        child_coll.delete(ids=results["ids"])

    # 找到并删除父文档
    p_results = parent_coll.get(where={"source_file": source_file})
    if p_results["ids"]:
        parent_coll.delete(ids=p_results["ids"])

    deleted = len(results.get("ids", []))
    if deleted:
        _rebuild_bm25(collection_name, persist_dir)

    logger.info("删除文档: %s, chunks=%d", source_file, deleted)
    return deleted


def get_collections(*, persist_dir: str = "") -> list[dict]:
    """列出所有知识库 collection（不含 _parents 后缀）。"""
    client = _get_client(persist_dir)
    result = []
    seen: set[str] = set()
    for c in client.list_collections():
        name = c.name
        if name.endswith("_parents"):
            name = name[:-8]
        if not name.startswith("kb_"):
            continue
        display = _display_name(name)
        if display not in seen:
            seen.add(display)
            stats = get_collection_stats(name, persist_dir=persist_dir)
            result.append({"name": display, **stats})
    return result


def get_collection_stats(collection_name: str, *, persist_dir: str = "") -> dict:
    """获取 collection 统计信息。"""
    safe = _safe_name(collection_name)
    client = _get_client(persist_dir)
    try:
        child_coll = client.get_collection(safe)
        return {
            "chunk_count": child_coll.count(),
            "parent_count": _safe_count(client, f"{safe}_parents"),
        }
    except Exception:
        return {"chunk_count": 0, "parent_count": 0}


def _safe_count(client, name: str) -> int:
    try:
        return client.get_collection(name).count()
    except Exception:
        return 0


# -- BM25 索引 --


def _rebuild_bm25(collection_name: str, persist_dir: str = "") -> None:
    """重建指定 collection 的 BM25 关键词索引。"""
    from rank_bm25 import BM25Okapi

    safe = _safe_name(collection_name)
    client = _get_client(persist_dir)
    try:
        child_coll = client.get_collection(safe)
        data = child_coll.get()
    except Exception:
        return

    if not data["documents"]:
        return

    # 分词（简单按空白+标点切，中文按字）
    import re
    tokenize = lambda t: [w.lower() for w in re.split(r"[^\w一-鿿]+", t) if w]

    corpus = [tokenize(d) for d in data["documents"]]
    ids = data["ids"]

    with _bm25_lock:
        _bm25_indices[collection_name] = BM25Okapi(corpus)
        _bm25_corpora[collection_name] = corpus
        _bm25_doc_ids[collection_name] = ids

    logger.debug("BM25 索引已重建: collection=%s, docs=%d", collection_name, len(corpus))


def _bm25_search(
    collection_name: str, query: str, top_k: int = 10
) -> list[tuple[str, float]]:
    """BM25 关键词检索，返回 [(chunk_id, score), ...]"""
    import re
    tokenize = lambda t: [w.lower() for w in re.split(r"[^\w一-鿿]+", t) if w]

    with _bm25_lock:
        index = _bm25_indices.get(collection_name)
        doc_ids = _bm25_doc_ids.get(collection_name)

    if not index or not doc_ids:
        return []

    tokenized = tokenize(query)
    if not tokenized:
        return []

    scores = index.get_scores(tokenized)
    # 取 top-K
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
    # 归一化分数
    max_s = ranked[0][1] if ranked else 1.0
    return [(doc_ids[i], s / max_s) for i, s in ranked if s > 0]


# -- Query 改写 --


def _rewrite_query(original: str) -> list[str]:
    """DeepSeek 把口语 query 改写为多条检索用 query（中英文各一条）。"""
    from tools.deepseek_client import chat_json

    prompt = f"""你是搜索查询改写助手。把用户的自然语言问题改写为 2-3 条适合检索的查询。

规则：
1. 提取核心关键词，去掉口语化表达
2. 如果涉及外贸/产品/公司，同时生成中英文查询
3. 每条查询 5-15 个字/词
4. 输出 JSON: {{"queries": ["query1", "query2", ...]}}

用户问题：{original}"""

    try:
        result = chat_json(
            [{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=256,
        )
        queries = result.get("queries", [original])
        # 确保至少包含原始 query
        if original not in queries:
            queries.insert(0, original)
        return queries[:3]
    except Exception as e:
        logger.warning("Query 改写失败: %s", e)
        return [original]


# -- RRF 融合 --


def _rrf_fuse(
    bm25_results: list[tuple[str, float]],
    vector_results: list[tuple[str, float]],
    k: int = 60,
    top_k: int = 15,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion 融合 BM25 和向量检索结果。

    RRF 公式: score(d) = sum(1 / (k + rank_i(d)))
    其中 k=60 是经典值，rank 从 1 开始。
    """
    rrf_scores: dict[str, float] = {}

    for rank, (doc_id, _) in enumerate(bm25_results, start=1):
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1.0 / (k + rank)

    for rank, (doc_id, _) in enumerate(vector_results, start=1):
        rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1.0 / (k + rank)

    ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    return ranked[:top_k]


# -- 重排序 --


def _rerank(query: str, candidates: list[dict]) -> list[dict]:
    """DeepSeek 逐条打分重排序。返回带 rerank_score 的结果。"""
    if len(candidates) <= 1:
        for c in candidates:
            c["rerank_score"] = c.get("score", 0.5)
        return candidates

    from tools.deepseek_client import chat_json

    # 构建打分 prompt
    items_text = ""
    for i, c in enumerate(candidates):
        chunk_short = c["chunk"][:300].replace("\n", " ")
        items_text += f"[{i}] {chunk_short}\n"

    prompt = f"""你是检索质量评估助手。根据用户查询，对以下文档片段的相关性打分（0-1）。

查询：{query}

文档片段：
{items_text}

规则：
- 1.0 = 完全相关，直接回答问题
- 0.8 = 高度相关，包含重要信息
- 0.5 = 部分相关，有参考价值
- 0.2 = 略有关联
- 0.0 = 完全无关
- 输出 JSON: {{"scores": [0.8, 0.3, ...]}}（按顺序对应每个片段）"""

    try:
        result = chat_json(
            [{"role": "user", "content": prompt}],
            temperature=0.05,
            max_tokens=512,
        )
        scores = result.get("scores", [])
        for i, c in enumerate(candidates):
            c["rerank_score"] = scores[i] if i < len(scores) else 0.5
    except Exception as e:
        logger.warning("重排序失败: %s", e)
        for c in candidates:
            c["rerank_score"] = c.get("score", 0.5)

    # 按 rerank_score 降序
    candidates.sort(key=lambda x: x["rerank_score"], reverse=True)
    return candidates


# -- 主检索入口 --


def search(
    collection_name: str,
    query: str,
    top_k: int = 5,
    *,
    filters: dict | None = None,
    mode: str = "hybrid_rerank",
    persist_dir: str = "",
) -> list[dict]:
    """主检索入口。

    Args:
        collection_name: 目标 collection
        query: 用户查询（自然语言）
        top_k: 返回结果数
        filters: ChromaDB where 条件，如 {"section": "...", "source_type": "pdf"}
        mode: "vector" / "hybrid" / "hybrid_rerank"（默认）

    Returns:
        [{"chunk": "父文档文本", "metadata": {...}, "score": 0.9, "rerank_score": 0.87, "source_doc": "..."}, ...]
    """
    from tools.embedding import get_embedding

    safe = _safe_name(collection_name)
    client = _get_client(persist_dir)
    child_coll = _get_or_create(client, safe)
    parent_coll = _get_or_create(client, f"{safe}_parents")

    # 1. Query 改写
    queries = _rewrite_query(query) if mode in ("hybrid", "hybrid_rerank") else [query]
    logger.debug("Query 改写: %s → %s", query, queries)

    # 空 collection 提前返回
    child_count = child_coll.count()
    if child_count == 0:
        return []

    # 2. 多 query 检索 + RRF 融合
    all_hybrid: dict[str, float] = {}

    for q in queries:
        # 语义检索
        q_embedding = get_embedding(q, is_query=True)
        vec_args: dict = {
            "query_embeddings": [q_embedding],
            "n_results": min(15, child_count),
        }
        if filters:
            vec_args["where"] = filters
        vec_results = child_coll.query(**vec_args)

        vec_pairs: list[tuple[str, float]] = []
        if vec_results["ids"] and vec_results["ids"][0]:
            for doc_id, dist in zip(vec_results["ids"][0], vec_results["distances"][0]):
                vec_pairs.append((doc_id, 1.0 - dist))  # cosine distance → similarity

        # BM25 检索
        bm25_pairs = _bm25_search(collection_name, q, top_k=15)

        # RRF 融合
        fused = _rrf_fuse(bm25_pairs, vec_pairs, top_k=15)
        for doc_id, score in fused:
            all_hybrid[doc_id] = max(all_hybrid.get(doc_id, 0), score)

    # 按 RRF 分数排序
    ranked_ids = sorted(all_hybrid.items(), key=lambda x: x[1], reverse=True)[:15]

    if not ranked_ids:
        return []

    # 3. 获取子文档详情
    top_ids = [r[0] for r in ranked_ids]
    child_data = child_coll.get(ids=top_ids)

    # 4. 去重 → 找父文档
    seen_parents: set[str] = set()
    candidates: list[dict] = []
    id_to_score = dict(ranked_ids)

    for i, cid in enumerate(child_data["ids"]):
        parent_id = child_data["metadatas"][i].get("parent_id", cid) if child_data["metadatas"] else cid
        if parent_id in seen_parents:
            continue
        seen_parents.add(parent_id)

        try:
            p_data = parent_coll.get(ids=[parent_id])
            chunk_text = p_data["documents"][0] if p_data["documents"] else child_data["documents"][i]
            meta = p_data["metadatas"][0] if p_data["metadatas"] else child_data["metadatas"][i]
        except Exception:
            chunk_text = child_data["documents"][i]
            meta = child_data["metadatas"][i] if child_data["metadatas"] else {}

        candidates.append({
            "chunk": chunk_text,
            "metadata": meta or {},
            "score": id_to_score.get(cid, 0),
            "source_doc": (meta or {}).get("source_file", ""),
        })

    # 5. 重排序
    if mode == "hybrid_rerank" and len(candidates) > top_k:
        candidates = _rerank(query, candidates)

    return candidates[:top_k]


def search_multi(
    collections: list[str],
    query: str,
    top_k: int = 5,
    *,
    mode: str = "hybrid_rerank",
    persist_dir: str = "",
) -> list[dict]:
    """跨多个 collection 搜索，合并排序。"""
    all_results: list[dict] = []
    for coll in collections:
        results = search(coll, query, top_k=top_k * 2, mode=mode, persist_dir=persist_dir)
        all_results.extend(results)

    # 按 rerank_score 或 score 降序
    key = lambda r: r.get("rerank_score", r.get("score", 0))
    all_results.sort(key=key, reverse=True)
    return all_results[:top_k]


def list_documents(collection_name: str, *, persist_dir: str = "") -> list[dict]:
    """列出 collection 中所有文档（去重后的源文件列表）。"""
    safe = _safe_name(collection_name)
    client = _get_client(persist_dir)
    try:
        parent_coll = client.get_collection(f"{safe}_parents")
        data = parent_coll.get()
    except Exception:
        return []

    if not data["ids"]:
        return []

    # 按 source_file 去重聚合
    docs: dict[str, dict] = {}
    for i, doc_id in enumerate(data["ids"]):
        meta = data["metadatas"][i] if data["metadatas"] else {}
        sf = meta.get("source_file", "")
        if sf not in docs:
            docs[sf] = {
                "doc_id": sf,
                "source_file": sf,
                "title": meta.get("doc_title", sf),
                "collection": meta.get("collection", collection_name),
                "chunk_count": 0,
            }
        docs[sf]["chunk_count"] += 1

    return sorted(docs.values(), key=lambda d: d["source_file"])


def get_parent_chunk(collection_name: str, parent_id: str, *, persist_dir: str = "") -> dict | None:
    """获取单个父文档的完整内容。"""
    safe = _safe_name(collection_name)
    client = _get_client(persist_dir)
    try:
        parent_coll = client.get_collection(f"{safe}_parents")
        data = parent_coll.get(ids=[parent_id])
        if data["documents"]:
            return {
                "chunk": data["documents"][0],
                "metadata": data["metadatas"][0] if data["metadatas"] else {},
                "doc_id": parent_id,
            }
    except Exception:
        pass
    return None
