"""嵌入服务（全局单例）— 基于 ONNX Runtime，无需 PyTorch。

使用 fastembed 引擎加载 BGE 系列模型，Windows 兼容。
默认模型: BAAI/bge-m3 (1024 维, 8192 token, 中英双语)。
若 BGE-M3 不可用，自动降级到 BAAI/bge-large-zh-v1.5。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# 模型缓存放到项目 var 下，避免 Windows Temp 清理/权限问题
_CACHE_DIR = Path(__file__).resolve().parents[1] / "var" / "embedding_cache"

# 优先轻量 multilingual 模型（~120MB，下载快，中英兼顾）
_MODEL_CANDIDATES = [
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "intfloat/multilingual-e5-large",
    "jinaai/jina-embeddings-v3",
]

_INSTANCE: "EmbeddingService | None" = None
_LOCK = threading.Lock()

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class EmbeddingService:
    """嵌入服务，基于 fastembed (ONNX Runtime)，全局只加载一次。"""

    def __init__(self, model_name: str = ""):
        from fastembed import TextEmbedding

        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        errors = []
        for cand in (_MODEL_CANDIDATES if not model_name else [model_name]):
            try:
                logger.info("尝试加载嵌入模型 %s ...", cand)
                self._model: TextEmbedding = TextEmbedding(model_name=cand, cache_dir=str(_CACHE_DIR))
                self._model_name = cand
                # 根据模型类型设置 query/passage 前缀
                if "e5" in cand.lower():
                    self._query_prefix = "query: "
                    self._passage_prefix = "passage: "
                elif "bge" in cand.lower():
                    self._query_prefix = QUERY_PREFIX
                    self._passage_prefix = ""
                else:
                    self._query_prefix = ""
                    self._passage_prefix = ""
                # 获取维度
                test_emb = list(self._model.embed(["test"]))[0]
                self._dim: int = len(test_emb)
                logger.info("嵌入模型就绪: %s, 维度=%d", cand, self._dim)
                return
            except Exception as e:
                logger.warning("模型 %s 加载失败: %s", cand, e)
                errors.append(f"{cand}: {e}")

        raise RuntimeError(
            f"无法加载任何嵌入模型。已尝试: {errors}\n"
            "请确保网络可访问 huggingface.co，或手动下载模型后指定路径。"
        )

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str, *, is_query: bool = False) -> list[float]:
        """单条文本嵌入。is_query=True 时自动加检索前缀。"""
        prefix = self._query_prefix if is_query else self._passage_prefix
        if prefix and text:
            text = prefix + text
        result = list(self._model.embed([text]))
        return result[0].tolist()

    def embed_batch(
        self, texts: list[str], *, is_query: bool = False
    ) -> list[list[float]]:
        """批量嵌入。"""
        prefix = self._query_prefix if is_query else self._passage_prefix
        if prefix:
            texts = [prefix + t if t else t for t in texts]
        results = list(self._model.embed(texts))
        return [r.tolist() for r in results]


def get_embedding_service() -> EmbeddingService:
    """获取全局单例（线程安全）。"""
    global _INSTANCE
    if _INSTANCE is None:
        with _LOCK:
            if _INSTANCE is None:
                _INSTANCE = EmbeddingService()
    return _INSTANCE


def get_embedding(text: str, *, is_query: bool = False) -> list[float]:
    """单条文本嵌入。"""
    return get_embedding_service().embed(text, is_query=is_query)


def get_embeddings(texts: list[str], *, is_query: bool = False) -> list[list[float]]:
    """批量文本嵌入。"""
    return get_embedding_service().embed_batch(texts, is_query=is_query)
