# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for Customer Platform Windows standalone."""

import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent  # packaging/ → repo root

# ── Hidden imports ────────────────────────────────────────────────
# Agent auto-discovery uses importlib → PyInstaller cannot trace.
# Every agent module AND its .py files must be listed.

_agent_names = [
    "chat_agent", "crm", "customer_eval", "inquiry_mail",
    "knowledge_base", "sales_admin", "social_media",
]
_agent_submods = ["manifest", "config", "routes", "tasks"]

hiddenimports = [
    # Core / framework
    "jinja2", "jinja2.ext",
    "uvicorn.logging", "uvicorn.loops", "uvicorn.loops.auto",
    "uvicorn.protocols", "uvicorn.protocols.http", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets", "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan", "uvicorn.lifespan.on",
    "fastapi", "fastapi.middleware", "starlette",
    "anyio", "anyio._backends", "asyncio",
    "sqlite3", "hashlib", "secrets", "json", "email", "email.mime",
    # Redis / RQ
    "redis", "rq", "rq.cli", "rq.SimpleWorker", "rq.worker",
    # ML / AI
    "torch", "sentence_transformers", "chromadb",
    "chromadb.db", "chromadb.api", "chromadb.utils",
    # Google API
    "google.auth", "google_auth_oauthlib", "googleapiclient",
    # Data
    "pandas", "openpyxl", "numpy",
    # HTTP / parsing
    "httpx", "trafilatura", "bcrypt", "python_multipart",
    "dotenv", "tqdm", "rank_bm25",
    # Standard lib submodules commonly missed
    "multiprocessing", "subprocess", "threading", "queue",
    "logging", "logging.handlers", "pathlib",
    "importlib", "importlib.metadata", "importlib.resources",
    # Core
    "src", "src.core", "src.core.app", "src.core.auth",
    "src.core.config", "src.core.database", "src.core.paths",
    "src.core.redis_utils", "src.core.tracking_pixel",
    "src.agents", "src.agents.base",
    # Tools
    "tools", "tools.pipeline",
]

# Agent modules
for a in _agent_names:
    pkg = f"src.agents.{a}"
    hiddenimports.append(pkg)
    for sub in _agent_submods:
        fp = ROOT / "src" / "agents" / a / f"{sub}.py"
        if fp.is_file():
            hiddenimports.append(f"{pkg}.{sub}")
    # Extra sub-modules some agents have
    for extra in ["agent_loop", "tools", "worker_child"]:
        fp = ROOT / "src" / "agents" / a / f"{extra}.py"
        if fp.is_file():
            hiddenimports.append(f"{pkg}.{extra}")

# Pipeline modules
for f in (ROOT / "tools" / "pipeline").glob("*.py"):
    if f.stem != "__init__":
        hiddenimports.append(f"tools.pipeline.{f.stem}")

# Standalone tools
_tool_files = [
    "build_product_catalog", "country_timezone", "deepseek_client",
    "doc_parser", "email_generator", "email_sender", "embedding",
    "eval_company_fit", "eval_retrieval", "gmail_sender",
    "imap_monitor", "import_kb", "import_kb_products",
    "make_demo_workbook", "map_zh_customer_sheet",
    "setup_gmail_oauth", "vector_store", "wecom_notify",
]
for t in _tool_files:
    hiddenimports.append(f"tools.{t}")

# ── Data files ────────────────────────────────────────────────────

datas = []

def _add_tree(src: Path, dest_prefix: str):
    if not src.is_dir():
        return
    for f in src.rglob("*"):
        if f.is_file() and "__pycache__" not in f.parts:
            rel = f.relative_to(ROOT)
            dst = str(rel.parent)
            datas.append((str(f), dst))

# Schemas
datas.append((str(ROOT / "schemas" / "eval_result.schema.json"), "schemas"))
datas.append((str(ROOT / "schemas" / "excel_io.json"), "schemas"))

# Shared templates / static
_add_tree(ROOT / "src" / "templates", "src/templates")
_add_tree(ROOT / "src" / "static", "src/static")

# Per-agent templates / static
for a in _agent_names:
    _add_tree(ROOT / "src" / "agents" / a / "templates", f"src/agents/{a}/templates")
    _add_tree(ROOT / "src" / "agents" / a / "static", f"src/agents/{a}/static")

# Catalog + knowledge base
cat = ROOT / "output" / "catalog.json"
if cat.is_file():
    datas.append((str(cat), "output"))
kb = ROOT / "product_kb" / "v1" / "kb.json"
if kb.is_file():
    datas.append((str(kb), "product_kb/v1"))

# ── Analysis ──────────────────────────────────────────────────────

a = Analysis(
    [str(ROOT / "packaging" / "launcher.py")],
    pathex=[str(ROOT), str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[str(ROOT / "packaging" / "hooks")],
    runtime_hooks=[],
    excludes=[
        "tkinter.test", "unittest", "pytest",
        "test", "tests",
        "Cython", "matplotlib", "scipy", "IPython",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="CustomerPlatform",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_architecture=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "packaging" / "icon.ico") if (ROOT / "packaging" / "icon.ico").is_file() else None,
)
