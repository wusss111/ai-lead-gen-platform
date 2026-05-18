"""CRM API and page routes."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Form, HTTPException, Query, Request
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


@router.get("/salespersons", response_class=HTMLResponse)
def salespersons_page(request: Request):
    from src.core.app import app
    t = app.state.jinja_env.get_template("crm_salespersons.html")
    return HTMLResponse(t.render({
        "request": request,
        "nav_agents": app.state.nav_agents,
        "active_agent": "crm",
    }))


@router.get("/{customer_id}", response_class=HTMLResponse)
def crm_detail_page(customer_id: int, request: Request):
    from src.core.app import app
    db = get_db()
    row = db.execute(
        "SELECT c.*, s.name as salesperson_name, s.email as salesperson_email "
        "FROM customer c LEFT JOIN salesperson s ON c.assigned_salesperson_id = s.id "
        "WHERE c.id=?", (customer_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "客户不存在")
    customer = dict_from_row(row)
    # Email tracking
    tr = db.execute(
        "SELECT COUNT(*) as open_count, MAX(opened_at) as last_open "
        "FROM email_tracking WHERE customer_id=?",
        (customer_id,)
    ).fetchone()
    customer["email_open_count"] = tr["open_count"] if tr else 0
    customer["email_last_open"] = tr["last_open"] if tr else None
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
    salesperson_id: str = Query(""),
    sort: str = Query("-created_at"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    batch_id: str = Query(""),
    email_status: str = Query(""),
    buyer_seller_role: str = Query(""),
    priority: str = Query(""),
    data_quality: str = Query(""),
    created_from: str = Query(""),
    created_to: str = Query(""),
    email_empty: str = Query(""),
) -> JSONResponse:
    """List customers with search, filter, sort, pagination."""
    db = get_db()

    where = []
    params: list[Any] = []

    if search.strip():
        where.append("(c.company_name LIKE ? OR c.contact_name LIKE ? OR c.contact_email LIKE ? OR c.website LIKE ?)")
        kw = f"%{search.strip()}%"
        params.extend([kw, kw, kw, kw])

    if deal_recommendation.strip():
        where.append("c.deal_recommendation = ?")
        params.append(deal_recommendation.strip())

    if min_score is not None:
        where.append("c.overall_score_computed >= ?")
        params.append(min_score)

    if country.strip():
        where.append("c.country_region LIKE ?")
        params.append(f"%{country.strip()}%")

    if review_flag.strip():
        where.append("c.manual_review_flag = ?")
        params.append(review_flag.strip())

    if batch_id.strip():
        where.append("c.batch_id = ?")
        params.append(batch_id.strip())

    if salesperson_id.strip():
        if salesperson_id.strip().lower() == "unassigned":
            where.append("c.assigned_salesperson_id IS NULL")
        else:
            where.append("c.assigned_salesperson_id = ?")
            params.append(int(salesperson_id))

    if email_status.strip():
        where.append("c.email_status = ?")
        params.append(email_status.strip())

    if buyer_seller_role.strip():
        where.append("c.buyer_seller_role = ?")
        params.append(buyer_seller_role.strip())

    if priority.strip():
        where.append("c.priority = ?")
        params.append(priority.strip())

    if data_quality.strip():
        where.append("c.data_quality = ?")
        params.append(data_quality.strip())

    if email_empty.strip():
        where.append("(c.contact_email IS NULL OR c.contact_email = '')")

    if created_from.strip():
        where.append("c.created_at >= ?")
        params.append(created_from.strip())

    if created_to.strip():
        where.append("c.created_at <= ?")
        params.append(created_to.strip() + " 23:59:59")

    where_clause = ("WHERE " + " AND ".join(where)) if where else ""

    # Count
    count_row = db.execute(
        f"SELECT COUNT(*) as cnt FROM customer c {where_clause}", params
    ).fetchone()
    total = count_row["cnt"] if count_row else 0

    # Sort
    allowed_sort = {
        "-created_at": "c.created_at DESC", "created_at": "c.created_at ASC",
        "-overall_score_computed": "c.overall_score_computed DESC", "overall_score_computed": "c.overall_score_computed ASC",
        "-company_name": "c.company_name DESC", "company_name": "c.company_name ASC",
    }
    order = allowed_sort.get(sort, "created_at DESC")

    # Paginate
    offset = (page - 1) * page_size
    rows = db.execute(
        f"SELECT c.*, s.name as salesperson_name "
        f"FROM customer c LEFT JOIN salesperson s ON c.assigned_salesperson_id = s.id "
        f"{where_clause} ORDER BY {order} LIMIT ? OFFSET ?",
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
    row = db.execute(
        "SELECT c.*, s.name as salesperson_name, s.email as salesperson_email "
        "FROM customer c LEFT JOIN salesperson s ON c.assigned_salesperson_id = s.id "
        "WHERE c.id=?", (customer_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "客户不存在")
    customer = dict_from_row(row)

    # Email tracking info
    tr = db.execute(
        "SELECT COUNT(*) as open_count, MAX(opened_at) as last_open "
        "FROM email_tracking WHERE customer_id=?",
        (customer_id,)
    ).fetchone()
    customer["email_open_count"] = tr["open_count"] if tr else 0
    customer["email_last_open"] = tr["last_open"] if tr else None

    return JSONResponse(customer)


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
manual_review_flag, email_status, email_sent_at, assigned_salesperson_id,
tracking_last_opened_at)
- deal_recommendation values: 'high_intent'/'watch'/'no'
- email_status values: 'generated'/'sent'/'failed'/null
- overall_score_computed: 0-100
- country_region: 2-letter country code (US, DE, FR, etc.)
- priority: 'high'/'medium'/'low'
- assigned_salesperson_id: references salesperson table (NULL if unassigned)
- tracking_last_opened_at: datetime when the email was last opened, NULL if never opened
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


# ---- Salesperson CRUD API ----

@router.get("/api/salespersons")
def list_salespersons(
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    db = get_db()
    rows = db.execute(
        "SELECT s.*, (SELECT COUNT(*) FROM customer c WHERE c.assigned_salesperson_id=s.id) AS customer_count "
        "FROM salesperson s ORDER BY s.is_active DESC, s.name ASC"
    ).fetchall()
    return JSONResponse(dicts_from_rows(rows))


@router.post("/api/salespersons")
def create_salesperson(
    _: Annotated[None, Depends(require_auth)],
    name: str = Form(...),
    email: str = Form(""),
    phone: str = Form(""),
) -> JSONResponse:
    if not name.strip():
        raise HTTPException(400, "姓名不能为空")
    db = get_db()
    cur = db.execute(
        "INSERT INTO salesperson (name, email, phone) VALUES (?, ?, ?)",
        (name.strip(), email.strip(), phone.strip()),
    )
    db.commit()
    row = db.execute("SELECT * FROM salesperson WHERE id=?", (cur.lastrowid,)).fetchone()
    return JSONResponse(dict_from_row(row))


@router.put("/api/salespersons/{sp_id}")
def update_salesperson(
    sp_id: int,
    _: Annotated[None, Depends(require_auth)],
    name: str = Form(None),
    email: str = Form(None),
    phone: str = Form(None),
    is_active: int = Form(None),
) -> JSONResponse:
    db = get_db()
    sets = []
    params: list[Any] = []
    if name is not None:
        sets.append("name=?")
        params.append(name.strip())
    if email is not None:
        sets.append("email=?")
        params.append(email.strip())
    if phone is not None:
        sets.append("phone=?")
        params.append(phone.strip())
    if is_active is not None:
        sets.append("is_active=?")
        params.append(is_active)
    if not sets:
        raise HTTPException(400, "没有需要更新的字段")
    params.append(sp_id)
    db.execute(f"UPDATE salesperson SET {', '.join(sets)} WHERE id=?", params)
    db.commit()
    return JSONResponse({"status": "ok"})


@router.delete("/api/salespersons/{sp_id}")
def delete_salesperson(
    sp_id: int,
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    db = get_db()
    db.execute("UPDATE customer SET assigned_salesperson_id=NULL WHERE assigned_salesperson_id=?", (sp_id,))
    db.execute("DELETE FROM salesperson WHERE id=?", (sp_id,))
    db.commit()
    return JSONResponse({"status": "ok"})


# ---- Customer assignment ----

@router.put("/api/customers/{customer_id}/assign")
def assign_customer(
    customer_id: int,
    _: Annotated[None, Depends(require_auth)],
    salesperson_id: str = Form(""),
) -> JSONResponse:
    db = get_db()
    sp_id = int(salesperson_id) if salesperson_id.strip() else None
    if sp_id is not None:
        exists = db.execute("SELECT 1 FROM salesperson WHERE id=?", (sp_id,)).fetchone()
        if not exists:
            raise HTTPException(400, "销售人员不存在")
    db.execute(
        "UPDATE customer SET assigned_salesperson_id=?, updated_at=datetime('now','localtime') WHERE id=?",
        (sp_id, customer_id),
    )
    db.commit()
    return JSONResponse({"status": "ok"})


@router.post("/api/customers/batch-assign")
def batch_assign_customers(
    _: Annotated[None, Depends(require_auth)],
    body: dict = Body(...),
) -> JSONResponse:
    """Batch assign multiple customers to a salesperson."""
    customer_ids = body.get("customer_ids", [])
    salesperson_id = body.get("salesperson_id")

    if not customer_ids or not isinstance(customer_ids, list):
        raise HTTPException(400, "请提供客户ID列表")
    if len(customer_ids) > 500:
        raise HTTPException(400, "单次最多分配500个客户")

    db = get_db()
    sp_id = int(salesperson_id) if str(salesperson_id).strip() else None
    if sp_id is not None:
        exists = db.execute("SELECT 1 FROM salesperson WHERE id=?", (sp_id,)).fetchone()
        if not exists:
            raise HTTPException(400, "销售人员不存在")

    placeholders = ",".join("?" for _ in customer_ids)
    db.execute(
        f"UPDATE customer SET assigned_salesperson_id=?, updated_at=datetime('now','localtime') "
        f"WHERE id IN ({placeholders})",
        [sp_id] + [int(x) for x in customer_ids],
    )
    db.commit()

    logger.info("Batch assigned %d customers to salesperson %s", len(customer_ids), sp_id)
    return JSONResponse({"status": "ok", "assigned_count": len(customer_ids)})


@router.delete("/api/customers/{customer_id}")
def delete_customer(
    customer_id: int,
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    """Delete a single customer and related records."""
    db = get_db()
    row = db.execute("SELECT 1 FROM customer WHERE id=?", (customer_id,)).fetchone()
    if not row:
        raise HTTPException(404, "客户不存在")
    db.execute("DELETE FROM email_tracking WHERE customer_id=?", (customer_id,))
    db.execute("DELETE FROM customer WHERE id=?", (customer_id,))
    db.commit()
    logger.info("Deleted customer %d", customer_id)
    return JSONResponse({"status": "ok"})


@router.post("/api/customers/batch-delete")
def batch_delete_customers(
    _: Annotated[None, Depends(require_auth)],
    body: dict = Body(...),
) -> JSONResponse:
    """Batch delete customers by IDs."""
    ids = body.get("customer_ids", [])
    if not ids or not isinstance(ids, list):
        raise HTTPException(400, "请提供客户ID列表")
    if len(ids) > 500:
        raise HTTPException(400, "单次最多删除500个客户")

    db = get_db()
    placeholders = ",".join("?" for _ in ids)
    flat_ids = [int(x) for x in ids]
    db.execute(
        f"DELETE FROM email_tracking WHERE customer_id IN ({placeholders})",
        flat_ids,
    )
    cur = db.execute(
        f"DELETE FROM customer WHERE id IN ({placeholders})",
        flat_ids,
    )
    db.commit()
    actual = cur.rowcount
    logger.info("Batch deleted %d customers", actual)
    return JSONResponse({"status": "ok", "deleted_count": actual})
