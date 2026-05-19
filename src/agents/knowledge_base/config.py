"""Knowledge base agent configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class KnowledgeBaseConfig:
    """知识库配置。"""

    data_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent.parent.parent / "var" / "knowledge_base")
    persist_dir: str = ""  # ChromaDB 持久化目录（空则用默认）
    queue_name: str = "knowledge_base:default"
    max_file_size_mb: int = 500
    ocr_cleanup: bool = True

    def __post_init__(self):
        if not self.persist_dir:
            self.persist_dir = str(self.data_dir / "chroma_db")

    @classmethod
    def from_env(cls) -> "KnowledgeBaseConfig":
        import os
        return cls(
            ocr_cleanup=os.environ.get("KB_OCR_CLEANUP", "1").strip() not in ("0", "false", "no"),
        )
