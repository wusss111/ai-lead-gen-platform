"""Chat agent configuration."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ChatAgentConfig:
    max_tool_rounds: int = 5
    max_history: int = 20
    model: str = "deepseek-v4-flash"
    temperature: float = 0.3
    max_tokens: int = 2048
