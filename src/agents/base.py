"""Agent manifest protocol. Every agent exposes a register() -> AgentManifest."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentManifest:
    """Metadata and resources that an agent exposes to the platform."""

    name: str                          # e.g. "customer_eval"
    display_name: str                  # e.g. "客户评估"
    description: str = ""

    # FastAPI APIRouter (optional: agents without routes only appear in nav)
    router: Any = None

    # Jinja2 template directory for this agent's pages
    template_dir: str | None = None

    # Static assets directory, mounted at /static/{name}/
    static_dir: str | None = None

    # Config model class (pydantic BaseModel)
    config_class: type | None = None

    # Navigation entry; if None the agent won't appear in the navbar
    nav: dict | None = None            # {"icon": "&#9741;", "order": 1}

    admin_only: bool = False  # True = 仅管理员可见（导航栏过滤）
