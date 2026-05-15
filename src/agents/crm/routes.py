"""CRM API and page routes."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.core.auth import require_auth
from src.core.database import get_db, dict_from_row, dicts_from_rows

logger = logging.getLogger(__name__)

router = APIRouter(tags=["crm"])


# ---- Page routes ----

@router.get("/", response_class=HTMLResponse)
def crm_list_page(request: Request):
    from src.core.app import app
    t = app.state.jinja_env.get_template("crm_list.html")
    return HTMLResponse(t.render({
        "request": request,
        "nav_agents": app.state.nav_agents,
        "active_agent": "crm",
    }))


@router.get("/{customer_id}", response_class=HTMLResponse)
def crm_detail_page(customer_id: int, request: Request):
    from src.core.app import app
    db = get_db()
    row = db.execute("SELECT * FROM customer WHERE id=?", (customer_id,)).fetchone()
    if not row:
        raise HTTPException(404, "客户不存在")
    customer = dict_from_row(row)
    t = app.state.jinja_env.get_template("crm_detail.html")
    return HTMLResponse(t.render({
        "request": request,
        "nav_agents": app.state.nav_agents,
        "active_agent": "crm",
        "customer": customer,
    }))


# ---- API routes ----

@router.get("/api/customers")
def list_customers(
    _: Annotated[None, Depends(require_auth)],
    search: str = Query(""),
    deal_recommendation: str = Query(""),
    min_score: float | None = Query(None),
    country: str = Query(""),
    review_flag: str = Query(""),
    sort: str = Query("-created_at"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    batch_id: str = Query(""),
) -> JSONResponse:
    """List customers with search, filter, sort, pagination."""
    db = get_db()

    where = []
    params: list[Any] = []

    if search.strip():
        where.append("(company_name LIKE ? OR contact_name LIKE ? OR contact_email LIKE ? OR website LIKE ?)")
        kw = f"%{search.strip()}%"
        params.extend([kw, kw, kw, kw])

    if deal_recommendation.strip():
        where.append("deal_recommendation = ?")
        params.append(deal_recommendation.strip())

    if min_score is not None:
        where.append("overall_score_computed >= ?")
        params.append(min_score)

    if country.strip():
        where.append("country_region LIKE ?")
        params.append(f"%{country.strip()}%")

    if review_flag.strip():
        where.append("manual_review_flag = ?")
        params.append(review_flag.strip())

    if batch_id.strip():
        where.append("batch_id = ?")
        params.append(batch_id.strip())

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    # Count
    count_row = db.execute(
        f"SELECT COUNT(*) as cnt FROM customer {where_clause}", params
    ).fetchone()
    total = count_row["cnt"] if count_row else 0

    # Sort
    allowed_sort = {
        "-created_at": "created_at DESC", "created_at": "created_at ASC",
        "-overall_score_computed": "overall_score_computed DESC", "overall_score_computed": "overall_score_computed ASC",
        "-company_name": "company_name DESC", "company_name": "company_name ASC",
    }
    order = allowed_sort.get(sort, "created_at DESC")

    # Paginate
    offset = (page - 1) * page_size
    rows = db.execute(
        f"SELECT * FROM customer {where_clause} ORDER BY {order} LIMIT ? OFFSET ?",
        params + [page_size, offset],
    ).fetchall()

    return JSONResponse({
        "customers": dicts_from_rows(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size) if total > 0 else 0,
    })


@router.get("/api/customers/{customer_id}")
def get_customer(
    customer_id: int,
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    db = get_db()
    row = db.execute("SELECT * FROM customer WHERE id=?", (customer_id,)).fetchone()
    if not row:
        raise HTTPException(404, "客户不存在")
    return JSONResponse(dict_from_row(row))


@router.get("/api/batches")
def list_batches(
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    db = get_db()
    rows = db.execute(
        "SELECT b.*, (SELECT COUNT(*) FROM customer c WHERE c.batch_id=b.id) as customer_count "
        "FROM evaluation_batch b ORDER BY b.created_at DESC"
    ).fetchall()
    return JSONResponse(dicts_from_rows(rows))


@router.get("/api/customers/export")
def export_customers(
    _: Annotated[None, Depends(require_auth)],
    batch_id: str = Query(""),
    deal_recommendation: str = Query(""),
) -> JSONResponse:
    """Export customer data as JSON (for download or Excel generation)."""
    db = get_db()
    where = []
    params: list[Any] = []
    if batch_id.strip():
        where.append("batch_id = ?")
        params.append(batch_id.strip())
    if deal_recommendation.strip():
        where.append("deal_recommendation = ?")
        params.append(deal_recommendation.strip())
    where_clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.execute(
        f"SELECT * FROM customer {where_clause} ORDER BY overall_score_computed DESC LIMIT 5000",
        params,
    ).fetchall()
    return JSONResponse(dicts_from_rows(rows))


# ---- Smart Search (Text-to-SQL) ----

_CUSTOMER_SCHEMA_HINT = """
Table: customer (id, batch_id, company_name, website, country_region,
contact_name, contact_email, contact_phone, contact_address, target_products,
priority, notes, product_fit_score, product_fit_reasons, capability_score,
capability_signals, reputation_facts, reputation_concerns, reputation_sources,
reputation_safety_score, buyer_seller_role, buyer_seller_reason,
deal_recommendation, next_action, confidence, data_quality, overall_score_computed,
manual_review_flag, email_status, email_sent_at)
- deal_recommendation values: 'high_intent'/'watch'/'no'
- email_status values: 'generated'/'sent'/'failed'/null
- overall_score_computed: 0-100
- country_region: 2-letter country code (US, DE, FR, etc.)
- priority: 'high'/'medium'/'low'
""".strip()


@router.post("/api/smart-search")
def smart_search(
    _: Annotated[None, Depends(require_auth)],
    q: str = Form(""),
) -> JSONResponse:
    """Natural language → SQL query on the customer table. Returns matching customers."""
    if not q.strip():
        raise HTTPException(400, "请输入搜索内容")

    from tools.deepseek_client import chat_json

    prompt = f"""You are a SQLite expert. Convert this natural language query to a safe SELECT statement.

{_CUSTOMER_SCHEMA_HINT}

User query: {q.strip()}

Rules:
- Return ONLY a JSON object: {{"sql": "<SELECT statement>", "explanation": "<brief Chinese explanation>"}}
- Only SELECT statements. Never INSERT/UPDATE/DELETE/DROP/ALTER.
- Use parameterized-safe column names from the schema above.
- Handle fuzzy search with LIKE '%keyword%'.
- For score/rating queries, use the appropriate numeric columns.
- For country queries, country_region contains 2-letter codes.
- Include relevant columns in SELECT, at minimum: id, company_name, contact_email, country_region, deal_recommendation, overall_score_computed.
- Add LIMIT 100 to prevent huge result sets.
- For "高分" use overall_score_computed >= 70, "低分" use overall_score_computed < 40.
- For "德国" use country_region='DE', "美国"='US', "法国"='FR', "英国"='GB', "日本"='JP', "中国"='CN'.
- Sort by overall_score_computed DESC by default unless user specifies otherwise."""

    try:
        result = chat_json(prompt, max_tokens=500, temperature=0.1)
    except Exception as e:
        logger.exception("Smart search LLM call failed")
        raise HTTPException(500, f"AI 搜索失败: {e}")

    sql = (result.get("sql") or "").strip()
    explanation = result.get("explanation", "")

    if not sql:
        raise HTTPException(400, "AI 未能生成有效查询，请换个说法试试")

    # Safety: only allow SELECT
    sql_upper = sql.upper().replace("\n", " ").replace("\r", " ")
    if not sql_upper.startswith("SELECT"):
        logger.warning("Smart search blocked non-SELECT: %s", sql[:120])
        raise HTTPException(400, "AI 生成的查询不安全，已被拦截。请换个说法试试。")
    for keyword in ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "EXEC", "ATTACH"]:
        if keyword in sql_upper:
            logger.warning("Smart search blocked for keyword %s: %s", keyword, sql[:120])
            raise HTTPException(400, f"查询包含禁止操作 ({keyword})，已被拦截")

    try:
        db = get_db()
        rows = db.execute(sql).fetchall()
    except Exception as e:
        logger.error("Smart search SQL error: %s — SQL: %s", e, sql[:200])
        raise HTTPException(400, f"查询执行失败: {e}")

    return JSONResponse({
        "query": q.strip(),
        "sql": sql,
        "explanation": explanation,
        "customers": dicts_from_rows(rows),
        "count": len(rows),
    })
