"""Sales Admin agent registration."""

from __future__ import annotations

from pathlib import Path

from src.agents.base import AgentManifest
from src.agents.sales_admin.routes import router

_AGENT_DIR = Path(__file__).resolve().parent


def register() -> AgentManifest:
    return AgentManifest(
        name="sales-admin",
        display_name="销售管理",
        description="管理销售团队账号和权限",
        router=router,
        template_dir=str(_AGENT_DIR / "templates"),
        static_dir=str(_AGENT_DIR / "static"),
        admin_only=True,
        nav={"icon": "&#128101;", "order": 2},
    )
