# -*- coding: utf-8 -*-
"""Sales Admin page routes — reuses CRM salesperson API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from src.core.auth import require_admin

router = APIRouter(tags=["sales-admin"])


@router.get("/", response_class=HTMLResponse)
def sales_admin_page(
    request: Request,
    _: Annotated[None, Depends(require_admin)],
):
    from src.core.app import app
    t = app.state.jinja_env.get_template("sales_admin.html")
    return HTMLResponse(t.render({
        "request": request,
        "nav_agents": app.state.nav_agents,
        "active_agent": "sales-admin",
    }))
