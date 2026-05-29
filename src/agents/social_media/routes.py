# -*- coding: utf-8 -*-
"""Social Media API and page routes."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.core.auth import require_auth, apply_sales_filter
from src.core.database import get_db, dicts_from_rows

logger = logging.getLogger(__name__)

router = APIRouter(tags=["social-media"])

VALID_PLATFORMS = {"facebook", "twitter", "instagram", "youtube", "linkedin", "tiktok", "pinterest"}


# ── Page route ──

@router.get("/", response_class=HTMLResponse)
def social_list_page(request: Request):
    from src.core.app import app
    t = app.state.jinja_env.get_template("social_list.html")
    return HTMLResponse(t.render({
        "request": request,
        "nav_agents": app.state.nav_agents,
        "active_agent": "social-media",
    }))


# ── API: customer list with social profiles ──

@router.get("/api/customers")
def list_social_customers(
    user: Annotated[dict, Depends(require_auth)],
    search: str = Query(""),
    platform: str = Query(""),
    min_score: float | None = Query(None),
    country: str = Query(""),
    has_social: str = Query(""),
    salesperson_id: str = Query(""),
    sort: str = Query("-created_at"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
) -> JSONResponse:
    """List customers with social media profiles. Default: only customers with social data."""
    db = get_db()

    if platform.strip() and platform.strip() not in VALID_PLATFORMS:
        raise HTTPException(400, f"不支持的平台: {platform}")

    where: list[str] = []
    params: list[Any] = []

    # 默认显示所有客户。传 has_social=1 只看有社媒的，has_social=0 只看无社媒的
    if has_social.strip() == "1":
        where.append("c.social_profiles IS NOT NULL AND c.social_profiles != '' AND c.social_profiles != '[]'")
    elif has_social.strip() == "0":
        where.append("(c.social_profiles IS NULL OR c.social_profiles = '' OR c.social_profiles = '[]')")

    # 销售只能看到分配给自己的客户
    apply_sales_filter(where, params, user)

    # 管理员按销售筛选
    if salesperson_id.strip():
        if salesperson_id.strip().lower() == "unassigned":
            where.append("c.assigned_salesperson_id IS NULL")
        else:
            where.append("c.assigned_salesperson_id = ?")
            params.append(int(salesperson_id))

    if search.strip():
        where.append("(c.company_name LIKE ? OR c.website LIKE ? OR c.contact_name LIKE ?)")
        kw = f"%{search.strip()}%"
        params.extend([kw, kw, kw])

    if platform.strip():
        where.append("c.social_profiles LIKE ?")
        params.append(f'%"platform": "{platform.strip()}"%')

    if min_score is not None:
        where.append("c.overall_score_computed >= ?")
        params.append(min_score)

    if country.strip():
        where.append("c.country_region LIKE ?")
        params.append(f"%{country.strip()}%")

    where_clause = (" WHERE " + " AND ".join(where)) if where else ""

    # Count
    count_row = db.execute(
        f"SELECT COUNT(*) as cnt FROM customer c{where_clause}", params
    ).fetchone()
    total = count_row["cnt"] if count_row else 0

    # Sort
    allowed_sort = {
        "-created_at": "c.created_at DESC",
        "created_at": "c.created_at ASC",
        "-overall_score_computed": "c.overall_score_computed DESC",
        "overall_score_computed": "c.overall_score_computed ASC",
        "company_name": "c.company_name ASC",
        "-company_name": "c.company_name DESC",
    }
    order = allowed_sort.get(sort, "c.created_at DESC")

    offset = (page - 1) * page_size
    rows = db.execute(
        f"SELECT c.id, c.company_name, c.website, c.country_region, "
        f"c.contact_email, c.overall_score_computed, c.deal_recommendation, "
        f"c.social_profiles, c.email_status, c.created_at, "
        f"c.assigned_salesperson_id, COALESCE(s.name, '') as salesperson_name "
        f"FROM customer c "
        f"LEFT JOIN salesperson s ON c.assigned_salesperson_id = s.id "
        f"{where_clause} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()

    # Parse social_profiles JSON for each row
    customers = []
    for r in rows:
        d = dict(r)
        try:
            d["social_profiles"] = json.loads(d["social_profiles"]) if d.get("social_profiles") else []
        except (json.JSONDecodeError, TypeError):
            d["social_profiles"] = []
        customers.append(d)

    return JSONResponse({
        "customers": customers,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
    })


# ── API: platform stats ──

@router.get("/api/stats")
def social_stats(
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    """Get per-platform customer counts."""
    db = get_db()

    total_all_row = db.execute("SELECT COUNT(*) as cnt FROM customer").fetchone()
    total_all = total_all_row["cnt"] if total_all_row else 0

    total_social_row = db.execute(
        "SELECT COUNT(*) as cnt FROM customer WHERE social_profiles IS NOT NULL AND social_profiles != '' AND social_profiles != '[]'"
    ).fetchone()
    total_with_social = total_social_row["cnt"] if total_social_row else 0

    platform_counts: dict[str, int] = {}
    for p in sorted(VALID_PLATFORMS):
        row = db.execute(
            "SELECT COUNT(*) as cnt FROM customer WHERE social_profiles LIKE ?",
            (f'%"platform": "{p}"%',)
        ).fetchone()
        platform_counts[p] = row["cnt"] if row else 0

    return JSONResponse({
        "total_customers": total_all,
        "total_with_social": total_with_social,
        "platform_counts": platform_counts,
    })
