"""Inquiry mail agent registration."""

from __future__ import annotations

from pathlib import Path

from src.agents.base import AgentManifest
from src.agents.inquiry_mail.routes import router
from src.agents.inquiry_mail.config import InquiryMailConfig

_AGENT_DIR = Path(__file__).resolve().parent


def register() -> AgentManifest:
    return AgentManifest(
        name="inquiry-mail",
        display_name="询盘邮件",
        description="AI 生成个性化询盘开发信，批量发送",
        router=router,
        template_dir=str(_AGENT_DIR / "templates"),
        static_dir=str(_AGENT_DIR / "static"),
        config_class=InquiryMailConfig,
        nav={"icon": "&#9993;", "order": 3},
    )
