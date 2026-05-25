"""FastAPI application factory. Discovers agents, mounts routers and static files."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

from src.agents import discover_agents
from src.core.config import get_config

_SRC_DIR = Path(__file__).resolve().parent.parent  # src/
_SHARED_STATIC = _SRC_DIR / "static"
_SHARED_TEMPLATES = _SRC_DIR / "templates"

logger = logging.getLogger(__name__)


def render(tmpl_name: str, context: dict) -> HTMLResponse:
    """Helper: render a Jinja2 template to HTMLResponse."""
    from src.core.app import app
    env: Environment = app.state.jinja_env
    t = env.get_template(tmpl_name)
    return HTMLResponse(t.render(context))


def create_app() -> FastAPI:
    # Load .env from repo root (if present)
    from pathlib import Path as _Path
    _dotenv_path = _Path(__file__).resolve().parent.parent.parent / ".env"
    if _dotenv_path.is_file():
        from dotenv import load_dotenv
        load_dotenv(_dotenv_path)
    config = get_config()
    config.data_dir.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title=config.app_title, version=config.app_version)

    # Build search path: agent template dirs first, then shared
    search_path = [str(_SHARED_TEMPLATES)]

    # Discover and mount agents FIRST (agent static routes must take priority over shared /static)
    agents = discover_agents()
    nav_agents = []

    for name, manifest in agents.items():
        # Mount agent router
        if manifest.router is not None:
            prefix = "/" + name
            app.include_router(manifest.router, prefix=prefix)

        # Mount agent static directory BEFORE shared static (specific paths must come first)
        if manifest.static_dir and Path(manifest.static_dir).is_dir():
            app.mount(
                f"/static/{name}",
                StaticFiles(directory=manifest.static_dir),
                name=f"agent_static_{name}",
            )

        # Add agent template dir to search path
        if manifest.template_dir and Path(manifest.template_dir).is_dir():
            search_path.insert(0, str(manifest.template_dir))

        # Collect nav entries
        if manifest.nav:
            nav_agents.append(manifest)

    # Mount shared static files LAST (agent-specific paths take priority)
    if _SHARED_STATIC.is_dir():
        app.mount("/static", StaticFiles(directory=str(_SHARED_STATIC)), name="shared_static")

    # Sort nav by order
    nav_agents.sort(key=lambda m: m.nav.get("order", 99) if m.nav else 99)

    # Build Jinja2 environment with full search path
    jinja_env = Environment(loader=FileSystemLoader(search_path), autoescape=True)

    # ---- Platform routes ----

    @app.get("/health")
    def health():
        return {"status": "ok", "agents": list(agents.keys())}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        t = jinja_env.get_template("home.html")
        return HTMLResponse(t.render({
            "request": request,
            "nav_agents": nav_agents,
            "active_agent": None,
            "agents": agents,
        }))

    # ---- Email tracking pixel endpoint (public, no auth) ----
    @app.get("/track/open/{tracking_uuid}")
    def track_open(tracking_uuid: str, request: Request):
        """Tracking pixel endpoint. Records email open events.
        Returns a 1x1 transparent GIF regardless of success/failure."""
        import base64
        from src.core.database import get_db as _get_track_db

        db = _get_track_db()
        log_row = db.execute(
            "SELECT id, customer_id FROM daily_send_log WHERE tracking_id=?",
            (tracking_uuid,)
        ).fetchone()

        if log_row:
            db.execute(
                "INSERT INTO email_tracking (tracking_id, customer_id, send_log_id, ip_address, user_agent) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    tracking_uuid,
                    log_row["customer_id"],
                    log_row["id"],
                    request.client.host if request.client else "",
                    request.headers.get("User-Agent", "")[:500],
                ),
            )
            db.execute(
                "UPDATE customer SET tracking_last_opened_at=datetime('now','localtime') WHERE id=?",
                (log_row["customer_id"],),
            )
            db.commit()

        # 1x1 transparent GIF (43 bytes, universally compatible)
        pixel = base64.b64decode(
            "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
        )
        return Response(content=pixel, media_type="image/gif",
                        headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    # Store on app.state
    app.state.nav_agents = nav_agents
    app.state.agents = agents
    app.state.jinja_env = jinja_env
    app.state.config = config

    # Schedule IMAP polling (non-blocking background task)
    @app.on_event("startup")
    async def _start_imap_scheduler() -> None:
        from rq import Queue
        from redis import Redis

        redis_conn = Redis.from_url(config.redis_url)
        q = Queue("inquiry_mail:default", connection=redis_conn)

        async def _schedule_imap_poll():
            # Wait for server to fully start before first poll
            await asyncio.sleep(10)
            while True:
                try:
                    q.enqueue(
                        "src.agents.inquiry_mail.tasks.imap_poll_job",
                        str(config.data_dir),
                        job_timeout=300,
                        job_id=f"imap_poll_{int(time.time())}",
                        failure_ttl=3600,
                        result_ttl=3600,
                    )
                except Exception as e:
                    logger.warning("IMAP poll enqueue failed: %s", e)
                await asyncio.sleep(60)

        asyncio.create_task(_schedule_imap_poll())
        logger.info("IMAP poll scheduler started (interval: 60s)")

    # Clean up DB connections on shutdown
    @app.on_event("shutdown")
    def _close_db() -> None:
        from src.core.database import close_db
        close_db()

    return app


app = create_app()
