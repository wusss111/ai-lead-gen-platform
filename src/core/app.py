"""FastAPI application factory. Discovers agents, mounts routers and static files."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Depends, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader

from src.agents import discover_agents
from src.core.config import get_config

_SRC_DIR = Path(__file__).resolve().parent.parent  # src/
_SHARED_STATIC = _SRC_DIR / "static"
_SHARED_TEMPLATES = _SRC_DIR / "templates"

from src.core.auth import get_current_user as _auth_get_user

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

    @app.middleware("http")
    async def _user_middleware(request: Request, call_next):
        """Attach current_user to request.state for template rendering.
        Redirect unauthenticated page visits to /login (API calls get 401 JSON)."""
        path = request.url.path
        _public = path in ("/login", "/health", "/") or path.startswith("/track/") or path.startswith("/static/")
        try:
            user = _auth_get_user(request, config)
        except Exception:
            user = None
        request.state.current_user = user
        if not user and not _public and not path.startswith("/api/"):
            return RedirectResponse(url=f"/login?redirect={path}", status_code=303)
        # 销售访问 admin-only 页面：返回友好提示而不是 JSON
        if user and user.get("role") != "admin" and path.startswith("/customer-eval") and not path.startswith("/api/"):
            return HTMLResponse(
                "<h3 style='margin:3rem auto;text-align:center;color:var(--text-secondary)'>"
                "仅管理员可访问此功能<br><a href='/'>返回首页</a></h3>",
                status_code=403)
        response = await call_next(request)
        return response

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

    # ---- Login / Logout routes ----

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        redirect_url = request.query_params.get("redirect", "/")
        t = jinja_env.get_template("login.html")
        return HTMLResponse(t.render({
            "request": request,
            "redirect": redirect_url,
        }))


    @app.post("/login")
    def login_action(
        request: Request,
        config_override=Depends(get_config),
        username: str = Form(""),
        password: str = Form(""),
        redirect: str = Form("/"),
    ):
        """Handle login form submission."""
        from src.core.auth import create_session
        from src.core.database import get_db as _login_db
        import bcrypt as _bcrypt

        db = _login_db()
        sp = db.execute(
            "SELECT id, name, password_hash, role FROM salesperson WHERE name=? AND is_active=1",
            (username.strip(),),
        ).fetchone()

        if not sp or not sp["password_hash"]:
            redirect_url = request.query_params.get("redirect", "/")
            t = jinja_env.get_template("login.html")
            return HTMLResponse(t.render({
                "request": request, "redirect": redirect_url, "error": "用户名或密码错误"
            }), status_code=401)

        try:
            pw_hash = sp["password_hash"]
            if isinstance(pw_hash, str):
                pw_hash = pw_hash.encode("utf-8")
            pw_ok = _bcrypt.checkpw(password.encode("utf-8"), pw_hash)
        except Exception:
            pw_ok = False

        if not pw_ok:
            redirect_url = request.query_params.get("redirect", "/")
            t = jinja_env.get_template("login.html")
            return HTMLResponse(t.render({
                "request": request, "redirect": redirect_url, "error": "用户名或密码错误"
            }), status_code=401)

        session_id = create_session(config, {
            "id": sp["id"], "name": sp["name"], "role": sp["role"] or "salesperson",
        })
        redirect_target = redirect.strip() or "/"
        if not redirect_target.startswith("/"):
            redirect_target = "/"
        resp = RedirectResponse(url=redirect_target, status_code=303)
        resp.set_cookie("session_id", session_id, httponly=True, samesite="lax", max_age=86400)
        return resp


    @app.post("/logout")
    def logout_action(request: Request, config_override=Depends(get_config)):
        """Handle logout."""
        from src.core.auth import destroy_session
        session_id = request.cookies.get("session_id", "")
        if session_id:
            destroy_session(config, session_id)
        resp = RedirectResponse(url="/login", status_code=303)
        resp.delete_cookie("session_id")
        return resp

    # Store on app.state
    app.state.nav_agents = nav_agents
    app.state.agents = agents
    app.state.jinja_env = jinja_env
    app.state.config = config

    # Bootstrap admin + Schedule IMAP polling (non-blocking background task)
    @app.on_event("startup")
    async def _startup_tasks() -> None:
        """Bootstrap admin account and start IMAP poll scheduler."""

        # 1. Admin bootstrap
        import bcrypt as _bcrypt
        import os as _os
        from src.core.database import get_db as _bootstrap_db

        db_local = _bootstrap_db()
        existing = db_local.execute(
            "SELECT 1 FROM salesperson WHERE role='admin' AND is_active=1"
        ).fetchone()
        if not existing:
            admin_pw = (_os.environ.get("ADMIN_PASSWORD") or "").strip()
            if not admin_pw:
                import secrets as _secrets
                admin_pw = _secrets.token_urlsafe(12)
                logger.warning("=" * 60)
                logger.warning("  No ADMIN_PASSWORD set. Generated admin password: %s", admin_pw)
                logger.warning("  Please change it after first login!")
                logger.warning("=" * 60)
            pw_hash = _bcrypt.hashpw(admin_pw.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")
            db_local.execute(
                "INSERT INTO salesperson (name, password_hash, role) VALUES ('admin', ?, 'admin')",
                (pw_hash,),
            )
            db_local.commit()
            logger.info("Admin account created: username=admin")

        # 2. IMAP poll scheduler (existing logic)
        from rq import Queue
        from redis import Redis as _Redis

        redis_conn = _Redis.from_url(config.redis_url)
        q = Queue("inquiry_mail:default", connection=redis_conn)

        async def _schedule_imap_poll():
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
