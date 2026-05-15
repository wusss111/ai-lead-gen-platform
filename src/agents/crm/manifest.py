"""CRM agent registration."""

from __future__ import annotations

from pathlib import Path

from src.agents.base import AgentManifest
from src.agents.crm.routes import router

_AGENT_DIR = Path(__file__).resolve().parent


def register() -> AgentManifest:
    return AgentManifest(
        name="crm",
        display_name="客户资源",
        description="浏览、搜索、筛选已评估的客户资源",
        router=router,
        template_dir=str(_AGENT_DIR / "templates"),
        static_dir=str(_AGENT_DIR / "static"),
        nav={"icon": "&#128203;", "order": 2},
    )
