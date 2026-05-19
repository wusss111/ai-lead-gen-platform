"""Chat agent registration (no nav — widget only)."""

from __future__ import annotations

from pathlib import Path

from src.agents.base import AgentManifest
from src.agents.chat_agent.routes import router

_AGENT_DIR = Path(__file__).resolve().parent


def register() -> AgentManifest:
    return AgentManifest(
        name="chat",
        display_name="智能客服",
        description="AI 智能客服助手，支持知识库搜索、客户查询、邮件生成",
        router=router,
        template_dir=str(_AGENT_DIR / "templates"),
        static_dir=str(_AGENT_DIR / "static"),
        nav=None,  # 不在导航栏显示，只显示聊天挂件
    )
