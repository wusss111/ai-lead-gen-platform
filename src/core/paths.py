"""Platform path utilities — frozen (PyInstaller) and dev aware."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def get_repo_root() -> Path:
    """Return the repository/application root directory.

    In PyInstaller frozen mode, returns ``sys._MEIPASS`` (the temp
    directory where bundled files are extracted).  In dev mode, walks
    up from this file's location: ``src/core/paths.py`` → root.
    """
    _ = getattr(sys, "frozen", False)
    if _:
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


def get_dotenv_path() -> Path:
    """Return the expected ``.env`` file path.

    Frozen mode: alongside the executable.
    Dev mode: repository root.
    """
    _ = getattr(sys, "frozen", False)
    if _:
        return Path(sys.executable).resolve().parent / ".env"
    return Path(__file__).resolve().parents[2] / ".env"


def get_data_dir() -> Path:
    """Return the writable runtime data directory.

    Precedence:
    1. ``PLATFORM_DATA_DIR`` environment variable (custom location).
    2. Frozen mode: ``data/`` alongside the executable.
    3. Dev mode: ``var/platform/`` under the repository root.
    """
    env = (os.environ.get("PLATFORM_DATA_DIR") or "").strip()
    if env:
        return Path(env)
    _ = getattr(sys, "frozen", False)
    if _:
        return Path(sys.executable).resolve().parent / "data"
    return Path(__file__).resolve().parents[2] / "var" / "platform"
