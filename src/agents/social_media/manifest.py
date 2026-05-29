"""Social Media agent registration."""

from __future__ import annotations

from pathlib import Path

from src.agents.base import AgentManifest
from src.agents.social_media.routes import router

_AGENT_DIR = Path(__file__).resolve().parent


def register() -> AgentManifest:
    return AgentManifest(
        name="social-media",
        display_name="社媒管理",
        description="浏览客户社交媒体账号，按平台筛选和分析",
        router=router,
        template_dir=str(_AGENT_DIR / "templates"),
        static_dir=str(_AGENT_DIR / "static"),
        nav={"icon": "&#128247;", "order": 5},
    )
