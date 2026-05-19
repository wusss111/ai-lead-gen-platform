"""Knowledge base agent registration."""

from __future__ import annotations

from pathlib import Path

from src.agents.base import AgentManifest
from src.agents.knowledge_base.routes import router
from src.agents.knowledge_base.config import KnowledgeBaseConfig

_AGENT_DIR = Path(__file__).resolve().parent


def register() -> AgentManifest:
    return AgentManifest(
        name="knowledge-base",
        display_name="知识库",
        description="产品文档、公司资料、采购表单的知识库管理与检索",
        router=router,
        template_dir=str(_AGENT_DIR / "templates"),
        static_dir=str(_AGENT_DIR / "static"),
        config_class=KnowledgeBaseConfig,
        nav={"icon": "&#128218;", "order": 4},
    )
