"""检索精度/召回率评测工具。

用法:
    python tools/eval_retrieval.py --test-data tests/rag_test_queries.json
    python tools/eval_retrieval.py --test-data tests/rag_test_queries.json --mode vector
    python tools/eval_retrieval.py --test-data tests/rag_test_queries.json --collection 产品信息

支持三种检索模式对比（消融实验）：
  vector - 纯语义检索（BGE-M3）
  hybrid - 混合检索（BM25 + 语义 + RRF）
  hybrid_rerank - 混合检索 + 重排序（默认，完整管道）
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("eval_retrieval")


def load_queries(path: str | Path) -> list[dict]:
    """加载测试查询集。"""
    with open(path, encoding="utf-8") as f:
        queries = json.load(f)
    if not isinstance(queries, list):
        raise ValueError("测试数据必须是 JSON 数组")
    return queries


def evaluate(
    queries: list[dict],
    *,
    collection: str | None = None,
    mode: str = "hybrid_rerank",
    top_k: int = 5,
) -> dict:
    """运行评测，返回指标字典。"""
    from tools.vector_store import search, search_multi, get_collections

    all_collections_data = get_collections()
    all_names = [c["name"] for c in all_collections_data]

    metrics = {
        "total_queries": len(queries),
        "top_k": top_k,
        "mode": mode,
        "collection": collection or "all",
        "queries_detail": [],
        "precision_at_k": 0.0,
        "recall_at_k": 0.0,
        "mrr": 0.0,
        "ndcg_at_k": 0.0,
    }

    total_precision = 0.0
    total_recall = 0.0
    total_mrr = 0.0
    total_ndcg = 0.0

    for qi, q in enumerate(queries):
        query_text = q["query"]
        relevant_docs = set(q.get("relevant_docs", []))
        relevant_sections = q.get("relevant_sections", [])

        # 检索
        start = time.perf_counter()
        if collection:
            results = search(collection, query_text, top_k=top_k, mode=mode)
        else:
            results = search_multi(all_names, query_text, top_k=top_k, mode=mode)
        elapsed = time.perf_counter() - start

        # 计算相关性（宽松：匹配文档名或 section）
        retrieved_docs = []
        for r in results:
            sf = r.get("source_doc", "")
            sec = r.get("metadata", {}).get("section", "")
            relevant = (sf in relevant_docs) or any(rs in sec for rs in relevant_sections)
            retrieved_docs.append(relevant)

        # Precision@K
        precision = sum(1 for r in retrieved_docs if r) / max(1, len(retrieved_docs))

        # Recall@K
        recall = sum(1 for r in retrieved_docs if r) / max(1, len(relevant_docs))

        # MRR
        mrr = 0.0
        for rank, rel in enumerate(retrieved_docs, start=1):
            if rel:
                mrr = 1.0 / rank
                break

        # NDCG@K
        dcg = sum(
            (1.0 / __import__("math").log2(i + 2)) if retrieved_docs[i] else 0.0
            for i in range(min(top_k, len(retrieved_docs)))
        )
        ideal = sum(
            1.0 / __import__("math").log2(i + 2)
            for i in range(min(top_k, len(relevant_docs)))
        )
        ndcg = dcg / max(1e-9, ideal)

        total_precision += precision
        total_recall += recall
        total_mrr += mrr
        total_ndcg += ndcg

        metrics["queries_detail"].append({
            "query": query_text,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "mrr": round(mrr, 3),
            "ndcg": round(ndcg, 3),
            "time_ms": round(elapsed * 1000, 1),
            "retrieved": [r.get("source_doc", "") for r in results],
        })

        logger.info("[%d/%d] \"%s\" P=%.2f R=%.2f MRR=%.2f (%.0fms)",
                     qi + 1, len(queries), query_text[:40],
                     precision, recall, mrr, elapsed * 1000)

    n = max(1, len(queries))
    metrics["precision_at_k"] = round(total_precision / n, 4)
    metrics["recall_at_k"] = round(total_recall / n, 4)
    metrics["mrr"] = round(total_mrr / n, 4)
    metrics["ndcg_at_k"] = round(total_ndcg / n, 4)

    return metrics


def print_report(metrics: dict) -> None:
    """打印评测报告。"""
    print("\n" + "=" * 60)
    print("检索评测报告")
    print("=" * 60)
    print(f"测试 query 数: {metrics['total_queries']}")
    print(f"检索模式:     {metrics['mode']}")
    print(f"目标 collection: {metrics['collection']}")
    print(f"Top-K:        {metrics['top_k']}")
    print("-" * 60)
    print(f"Precision@{metrics['top_k']}:  {metrics['precision_at_k']:.4f}")
    print(f"Recall@{metrics['top_k']}:     {metrics['recall_at_k']:.4f}")
    print(f"MRR:           {metrics['mrr']:.4f}")
    print(f"NDCG@{metrics['top_k']}:        {metrics['ndcg_at_k']:.4f}")
    print("=" * 60)

    # 详细结果表
    print(f"\n{'Query':<45} {'P':>6} {'R':>6} {'MRR':>6} {'ms':>8}")
    print("-" * 80)
    for d in metrics["queries_detail"]:
        q = d["query"][:42] + "..." if len(d["query"]) > 42 else d["query"]
        print(f"{q:<45} {d['precision']:6.3f} {d['recall']:6.3f} {d['mrr']:6.3f} {d['time_ms']:8.1f}")


def ablation_study(queries: list[dict], **kwargs) -> None:
    """消融实验：对比三种检索模式。"""
    print("\n" + "=" * 60)
    print("消融实验 — 各优化对精度的贡献")
    print("=" * 60)

    modes = [
        ("vector", "纯语义检索（BGE-M3）"),
        ("hybrid", "+ BM25 混合检索 + RRF 融合"),
        ("hybrid_rerank", "+ 重排序（完整管道）"),
    ]

    print(f"\n{'优化项':<35} {'Precision':>10} {'Recall':>10} {'MRR':>10} {'NDCG':>10}")
    print("-" * 80)

    for mode, label in modes:
        m = evaluate(queries, mode=mode, **kwargs)
        print(f"{label:<35} {m['precision_at_k']:10.4f} {m['recall_at_k']:10.4f} {m['mrr']:10.4f} {m['ndcg_at_k']:10.4f}")

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="知识库检索精度/召回率评测")
    parser.add_argument("--test-data", "-t", required=True, help="测试数据 JSON 文件路径")
    parser.add_argument("--collection", "-c", default="", help="限定 collection（默认搜索全部）")
    parser.add_argument("--mode", "-m", default="hybrid_rerank",
                        choices=["vector", "hybrid", "hybrid_rerank"],
                        help="检索模式（默认: hybrid_rerank）")
    parser.add_argument("--top-k", "-k", type=int, default=5, help="Top-K (默认 5)")
    parser.add_argument("--ablation", "-a", action="store_true",
                        help="运行消融实验，对比三种检索模式")
    parser.add_argument("--output", "-o", default="", help="输出报告 JSON 路径")
    args = parser.parse_args()

    queries = load_queries(args.test_data)
    logger.info("加载 %d 条测试 query", len(queries))

    kwargs = {"top_k": args.top_k}
    if args.collection:
        kwargs["collection"] = args.collection

    if args.ablation:
        ablation_study(queries, **kwargs)
    else:
        metrics = evaluate(queries, mode=args.mode, **kwargs)
        print_report(metrics)

    if args.output:
        output_path = Path(args.output)
        output_path.write_text(
            json.dumps(metrics if not args.ablation else {}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("报告已保存: %s", output_path)

    # 保存详细结果到默认路径
    default_out = REPO_ROOT / "var" / "knowledge_base" / "eval_report.json"
    default_out.parent.mkdir(parents=True, exist_ok=True)
    default_out.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
