"""Customer eval agent registration."""

from __future__ import annotations

from pathlib import Path

from src.agents.base import AgentManifest
from src.agents.customer_eval.routes import router
from src.agents.customer_eval.config import CustomerEvalConfig

_AGENT_DIR = Path(__file__).resolve().parent


def register() -> AgentManifest:
    return AgentManifest(
        name="customer-eval",
        display_name="客户评估",
        description="上传 Excel，AI 自动评估潜客质量与跟进优先级",
        router=router,
        template_dir=str(_AGENT_DIR / "templates"),
        static_dir=str(_AGENT_DIR / "static"),
        config_class=CustomerEvalConfig,
        nav={"icon": "&#128202;", "order": 1},
    )
