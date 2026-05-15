"""Agent auto-discovery: scans subdirectories for manifest.py and calls register()."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.agents.base import AgentManifest

_agents: dict[str, "AgentManifest"] = {}


def discover_agents() -> dict[str, "AgentManifest"]:
    """Walk src/agents/ subdirectories, import manifest.py, call register()."""
    global _agents
    if _agents:
        return _agents

    agents_dir = Path(__file__).resolve().parent
    for item in sorted(agents_dir.iterdir()):
        if not item.is_dir():
            continue
        if item.name.startswith("_") or item.name.startswith("."):
            continue
        manifest_path = item / "manifest.py"
        if not manifest_path.is_file():
            continue

        module_path = f"src.agents.{item.name}.manifest"
        try:
            mod = importlib.import_module(module_path)
        except Exception as e:
            print(f"[WARN] Failed to import agent '{item.name}': {e}")
            continue
        if not hasattr(mod, "register"):
            print(f"[WARN] Agent '{item.name}' missing register()")
            continue
        manifest: "AgentManifest" = mod.register()
        _agents[manifest.name] = manifest

    return _agents


def get_agent(name: str) -> "AgentManifest":
    agents = discover_agents()
    if name not in agents:
        raise KeyError(f"Unknown agent: {name}. Available: {list(agents.keys())}")
    return agents[name]
