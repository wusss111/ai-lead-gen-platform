"""Inquiry mail API and page routes."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.core.auth import require_auth
from src.core.config import PlatformConfig, get_config
from src.core.redis_utils import get_queue, get_rq_job_info
from src.core.database import get_db, dicts_from_rows, dict_from_row
from src.agents.inquiry_mail.config import InquiryMailConfig
from src.agents.inquiry_mail.tasks import generate_emails_job, send_emails_job

logger = logging.getLogger(__name__)

router = APIRouter(tags=["inquiry-mail"])


# ---- Page routes ----

@router.get("/", response_class=HTMLResponse)
def mail_page(request: Request):
    from src.core.app import app
    t = app.state.jinja_env.get_template("mail_index.html")
    return HTMLResponse(t.render({
        "request": request,
        "nav_agents": app.state.nav_agents,
        "active_agent": "inquiry-mail",
    }))


# ---- API routes ----

@router.get("/api/customers/emailable")
def get_emailable_customers(
    _: Annotated[None, Depends(require_auth)],
    search: str = "",
    deal_recommendation: str = "",
    email_status: str = "",
    read_status: str = "",
    salesperson_id: str = "",
    email_empty: str = "",
    country: str = "",
    min_score: float | None = Query(None),
    buyer_seller_role: str = "",
    priority: str = "",
    data_quality: str = "",
    review_flag: str = "",
    created_from: str = "",
    created_to: str = "",
    offset: int = 0,
    limit: int = 200,
) -> JSONResponse:
    """List customers for inquiry mail (supports pagination)."""
    db = get_db()
    where: list[str] = []
    params: list[Any] = []

    if search.strip():
        where.append("(c.company_name LIKE ? OR c.contact_name LIKE ? OR c.contact_email LIKE ? OR c.website LIKE ?)")
        kw = f"%{search.strip()}%"
        params.extend([kw, kw, kw, kw])

    if deal_recommendation.strip():
        where.append("c.deal_recommendation = ?")
        params.append(deal_recommendation.strip())

    if email_status.strip():
        where.append("c.email_status = ?")
        params.append(email_status.strip())

    if read_status.strip() == "read":
        where.append("c.tracking_last_opened_at IS NOT NULL")
    elif read_status.strip() == "unread":
        where.append("(c.email_status = 'sent' AND c.tracking_last_opened_at IS NULL)")
    elif read_status.strip() == "unsent":
        where.append("(c.email_status IS NULL OR c.email_status NOT IN ('sent','failed'))")

    if salesperson_id.strip():
        if salesperson_id.strip() == "unassigned":
            where.append("c.assigned_salesperson_id IS NULL")
        else:
            where.append("c.assigned_salesperson_id = ?")
            params.append(int(salesperson_id))

    # Default to customers WITH email (inquiry mail needs an address to send to).
    # email_empty="" (default) or "0" → has email; "1" → no email.
    if email_empty.strip() == '1':
        where.append("(c.contact_email IS NULL OR c.contact_email = '')")
    else:
        where.append("c.contact_email IS NOT NULL AND c.contact_email != ''")

    if country.strip():
        where.append("c.country_region LIKE ?")
        params.append(f"%{country.strip()}%")

    if min_score is not None:
        where.append("c.overall_score_computed >= ?")
        params.append(min_score)

    if buyer_seller_role.strip():
        where.append("c.buyer_seller_role = ?")
        params.append(buyer_seller_role.strip())

    if priority.strip():
        where.append("c.priority = ?")
        params.append(priority.strip())

    if data_quality.strip():
        where.append("c.data_quality = ?")
        params.append(data_quality.strip())

    if review_flag.strip():
        where.append("c.manual_review_flag = ?")
        params.append(review_flag.strip())

    if created_from.strip():
        where.append("c.created_at >= ?")
        params.append(created_from.strip())

    if created_to.strip():
        where.append("c.created_at <= ?")
        params.append(created_to.strip() + " 23:59:59")

    where_clause = " AND ".join(where) if where else "1=1"
    rows = db.execute(
        f"SELECT c.id, c.company_name, c.contact_name, c.contact_email, c.country_region, "
        f"c.deal_recommendation, c.overall_score_computed, c.email_status, "
        f"c.assigned_salesperson_id, COALESCE(s.name, '') as salesperson_name, "
        f"c.tracking_last_opened_at "
        f"FROM customer c "
        f"LEFT JOIN salesperson s ON c.assigned_salesperson_id = s.id "
        f"WHERE {where_clause} ORDER BY c.overall_score_computed DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    return JSONResponse(dicts_from_rows(rows))


@router.post("/api/generate")
def generate_emails(
    _: Annotated[None, Depends(require_auth)],
    config: Annotated[PlatformConfig, Depends(get_config)],
    customer_ids: str = Form(""),
    language: str = Form("auto"),
) -> JSONResponse:
    """Trigger email generation for selected customers."""
    mail_cfg = InquiryMailConfig.from_env()

    ids = _parse_ids(customer_ids)

    job_id = str(uuid.uuid4())
    mail_dir = config.data_dir / "jobs" / job_id
    mail_dir.mkdir(parents=True, exist_ok=True)

    queue = get_queue(config.redis_url, mail_cfg.queue_name)
    rq_job = queue.enqueue(
        generate_emails_job,
        job_id,
        str(config.data_dir),
        customer_ids=ids or None,
        language=language,
        from_name=mail_cfg.from_name,
        model=None,
        job_id=job_id,
        job_timeout=600,
        failure_ttl=86400,
        result_ttl=86400,
    )
    (mail_dir / "gen_rq_job_id.txt").write_text(rq_job.id, encoding="utf-8")

    return JSONResponse({
        "job_id": job_id,
        "rq_job_id": rq_job.id,
        "status": "queued",
        "customer_count": len(ids) if ids else "auto",
    })


@router.get("/api/emails/saved")
def get_saved_emails(
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    """Return customers who already have generated/draft/confirmed/sent/failed emails."""
    db = get_db()
    rows = db.execute(
        "SELECT c.id, c.company_name, c.contact_name, c.contact_email, c.country_region, "
        "c.overall_score_computed, c.deal_recommendation, "
        "c.email_status, c.email_subject, c.email_body, "
        "c.email_sent_at, c.tracking_last_opened_at, c.assigned_salesperson_id, "
        "COALESCE(s.name, '') as salesperson_name "
        "FROM customer c "
        "LEFT JOIN salesperson s ON c.assigned_salesperson_id = s.id "
        "WHERE c.email_status IN ('draft','confirmed','generated','sent','failed') "
        "ORDER BY c.email_status, c.overall_score_computed DESC"
    ).fetchall()
    return JSONResponse(dicts_from_rows(rows))


@router.get("/api/emails/{job_id}")
def get_emails(
    job_id: str,
    _: Annotated[None, Depends(require_auth)],
    config: Annotated[PlatformConfig, Depends(get_config)],
) -> JSONResponse:
    """Get generated emails and their status."""
    mail_dir = config.data_dir / "jobs" / job_id
    emails_path = mail_dir / "emails.json"

    # Check RQ job status
    info = get_rq_job_info(mail_dir / "gen_rq_job_id.txt", config.redis_url)

    emails = []
    if emails_path.is_file():
        emails = json.loads(emails_path.read_text(encoding="utf-8"))

    return JSONResponse({
        "job_id": job_id,
        "rq_status": info["rq_status"],
        "progress": info.get("progress"),
        "emails": emails,
        "count": len(emails),
    })


@router.post("/api/send")
def send_emails(
    _: Annotated[None, Depends(require_auth)],
    config: Annotated[PlatformConfig, Depends(get_config)],
    job_id: str = Form(""),
    customer_ids: str = Form(""),
    respect_tz: str = Form(""),
) -> JSONResponse:
    """Trigger sending generated emails."""
    mail_cfg = InquiryMailConfig.from_env()

    # respect_tz form override (1 = respect, 0 = ignore, empty = use config default)
    if respect_tz == "1":
        mail_cfg.respect_timezone = True
    elif respect_tz == "0":
        mail_cfg.respect_timezone = False

    # 检查是否有可用的发送通道：Gmail API token（全局或业务员独立）或 SMTP
    _repo_root = Path(__file__).resolve().parent.parent.parent.parent
    _gmail_global = _repo_root / "var" / "gmail_token.json"
    _gmail_tokens_dir = _repo_root / "var" / "gmail_tokens"
    _has_gmail = _gmail_global.is_file() or (
        _gmail_tokens_dir.is_dir() and any(_gmail_tokens_dir.glob("gmail_token_*.json"))
    )
    _has_smtp = bool(mail_cfg.smtp_host and mail_cfg.from_email)

    if not _has_gmail and not _has_smtp:
        raise HTTPException(400, "没有可用的发送通道：请配置 SMTP 环境变量，或运行 python tools/setup_gmail_oauth.py 完成 Gmail OAuth 授权")

    if not job_id:
        raise HTTPException(400, "缺少 job_id")

    # Check daily quota before enqueuing
    db = get_db()
    row = db.execute(
        "SELECT COUNT(1) FROM daily_send_log WHERE sent_date = date('now','localtime') AND status = 'sent'"
    ).fetchone()
    sent_today = row[0] if row else 0
    if sent_today >= mail_cfg.daily_limit:
        raise HTTPException(
            status_code=429,
            detail=f"今日发送配额已用完（{mail_cfg.daily_limit} 封/天）。已发送 {sent_today} 封，请明天再试。"
        )

    ids = _parse_ids(customer_ids)

    smtp_dict = {
        "host": mail_cfg.smtp_host,
        "port": mail_cfg.smtp_port,
        "username": mail_cfg.smtp_username,
        "password": mail_cfg.smtp_password,
        "from_email": mail_cfg.from_email,
        "from_name": mail_cfg.from_name,
        "reply_to_email": mail_cfg.reply_to_email or mail_cfg.from_email,
        "use_tls": mail_cfg.use_tls,
        "use_ssl": mail_cfg.use_ssl,
        "send_delay_seconds": mail_cfg.send_delay_seconds,
        "respect_timezone": mail_cfg.respect_timezone,
        "business_hours_start": mail_cfg.business_hours_start,
        "business_hours_end": mail_cfg.business_hours_end,
    }

    queue = get_queue(config.redis_url, "inquiry_mail:send")
    rq_job = queue.enqueue(
        send_emails_job,
        job_id,
        str(config.data_dir),
        smtp_config_dict=smtp_dict,
        selected_ids=ids or None,
        daily_limit=mail_cfg.daily_limit,
        respect_timezone=mail_cfg.respect_timezone,
        business_hours_start=mail_cfg.business_hours_start,
        business_hours_end=mail_cfg.business_hours_end,
        job_id=job_id + "_send",
        job_timeout=1800,
        failure_ttl=86400,
        result_ttl=86400,
    )

    mail_dir = config.data_dir / "jobs" / job_id
    (mail_dir / "send_rq_job_id.txt").write_text(rq_job.id, encoding="utf-8")

    return JSONResponse({
        "job_id": job_id,
        "send_rq_job_id": rq_job.id,
        "status": "queued",
    })


@router.get("/api/send-status/{job_id}")
def get_send_status(
    job_id: str,
    _: Annotated[None, Depends(require_auth)],
    config: Annotated[PlatformConfig, Depends(get_config)],
) -> JSONResponse:
    """Get email sending progress."""
    mail_dir = config.data_dir / "jobs" / job_id
    rq_id_file = mail_dir / "send_rq_job_id.txt"

    if not rq_id_file.is_file():
        return JSONResponse({"status": "not_started"})

    info = get_rq_job_info(rq_id_file, config.redis_url)

    # If job is finished, also fetch the result
    result = None
    if info["rq_status"] == "finished":
        try:
            from rq.job import Job
            from redis import Redis
            rq_id = rq_id_file.read_text(encoding="utf-8").strip()
            conn = Redis.from_url(config.redis_url)
            job = Job.fetch(rq_id, connection=conn)
            result = job.result
        except Exception:
            pass

    return JSONResponse({
        "status": info["rq_status"],
        "progress": info.get("progress"),
        "result": result,
    })


@router.put("/api/emails/{customer_id}")
def update_email(
    customer_id: int,
    _: Annotated[None, Depends(require_auth)],
    subject: str = Form(""),
    body: str = Form(""),
) -> JSONResponse:
    """Update email subject/body for a customer (only if not yet sent)."""
    db = get_db()
    row = db.execute(
        "SELECT email_status FROM customer WHERE id=?", (customer_id,)
    ).fetchone()
    if not row:
        raise HTTPException(404, "客户不存在")
    if row["email_status"] == "sent":
        raise HTTPException(400, "已发送的邮件不可修改")

    db.execute(
        "UPDATE customer SET email_subject=?, email_body=?, updated_at=datetime('now','localtime') WHERE id=?",
        (subject.strip(), body.strip(), customer_id),
    )
    db.commit()
    return JSONResponse({"status": "ok"})


@router.post("/api/emails/confirm")
def confirm_emails(
    _: Annotated[None, Depends(require_auth)],
    customer_ids: str = Form(""),
) -> JSONResponse:
    """Confirm draft/generated emails, making them ready to send."""
    ids = _parse_ids(customer_ids)
    if not ids:
        raise HTTPException(400, "请提供要确认的客户ID")

    db = get_db()
    placeholders = ",".join("?" for _ in ids)
    cur = db.execute(
        f"UPDATE customer SET email_status='confirmed', updated_at=datetime('now','localtime') "
        f"WHERE id IN ({placeholders}) AND email_status IN ('draft','generated')",
        ids,
    )
    db.commit()
    return JSONResponse({"status": "ok", "confirmed_count": cur.rowcount})


@router.get("/api/smtp-check")
def check_smtp(
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    """Check if SMTP is configured."""
    mail_cfg = InquiryMailConfig.from_env()
    configured = bool(mail_cfg.smtp_host and mail_cfg.from_email)
    return JSONResponse({
        "configured": configured,
        "host": mail_cfg.smtp_host or "(未配置)",
        "from": mail_cfg.from_email or "(未配置)",
    })


# ---- Reply draft API routes ----


@router.get("/reply/{draft_id}", response_class=HTMLResponse)
def reply_editor_page(draft_id: int, request: Request):
    """Mobile-friendly reply editor for salespersons."""
    from src.core.app import app
    db = get_db()
    draft = db.execute(
        "SELECT r.*, c.company_name, c.contact_name, c.contact_email, c.country_region, "
        "s.name as salesperson_name "
        "FROM reply_draft r "
        "JOIN customer c ON r.customer_id = c.id "
        "JOIN salesperson s ON r.salesperson_id = s.id "
        "WHERE r.id=?",
        (draft_id,),
    ).fetchone()
    if not draft:
        raise HTTPException(404, "草稿不存在")

    t = app.state.jinja_env.get_template("reply_editor.html")
    return HTMLResponse(t.render({
        "request": request,
        "draft": dict_from_row(draft),
    }))


@router.get("/api/replies")
def list_reply_drafts(
    _: Annotated[None, Depends(require_auth)],
    status: str = "pending",
    salesperson_id: str = "",
) -> JSONResponse:
    """List reply drafts for admin/review."""
    db = get_db()
    where = ["r.status = ?"]
    params: list[Any] = [status]
    if salesperson_id.strip():
        where.append("r.salesperson_id = ?")
        params.append(int(salesperson_id))
    rows = db.execute(
        f"SELECT r.*, c.company_name, s.name as salesperson_name "
        f"FROM reply_draft r "
        f"JOIN customer c ON r.customer_id = c.id "
        f"JOIN salesperson s ON r.salesperson_id = s.id "
        f"WHERE {' AND '.join(where)} ORDER BY r.created_at DESC LIMIT 100",
        params,
    ).fetchall()
    return JSONResponse(dicts_from_rows(rows))


@router.put("/api/replies/{draft_id}")
def update_reply_draft(
    draft_id: int,
    _: Annotated[None, Depends(require_auth)],
    draft_subject: str = Form(""),
    draft_body: str = Form(""),
) -> JSONResponse:
    """Update a reply draft's subject/body."""
    db = get_db()
    db.execute(
        "UPDATE reply_draft SET draft_subject=?, draft_body=?, status='edited', "
        "updated_at=datetime('now','localtime') WHERE id=?",
        (draft_subject.strip(), draft_body.strip(), draft_id),
    )
    db.commit()
    return JSONResponse({"status": "ok"})


@router.post("/api/replies/{draft_id}/approve")
def approve_reply(
    draft_id: int,
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    """Approve and send a reply draft using the salesperson's SMTP."""
    db = get_db()
    draft = db.execute(
        "SELECT r.*, s.smtp_host, s.smtp_port, s.smtp_username, s.smtp_password, "
        "s.name as sp_name, c.contact_email, c.company_name "
        "FROM reply_draft r "
        "JOIN salesperson s ON r.salesperson_id = s.id "
        "JOIN customer c ON r.customer_id = c.id "
        "WHERE r.id=? AND r.status IN ('pending','edited')",
        (draft_id,),
    ).fetchone()
    if not draft:
        raise HTTPException(404, "草稿不存在或已处理")

    from tools.email_sender import SmtpConfig, send_single_email
    smtp_cfg = SmtpConfig(
        host=draft["smtp_host"], port=draft["smtp_port"] or 587,
        username=draft["smtp_username"], password=draft["smtp_password"],
        from_email=draft["smtp_username"], from_name=draft["sp_name"] or "外贸团队",
    )
    result = send_single_email(
        smtp_cfg,
        to_email=draft["contact_email"],
        subject=draft["draft_subject"],
        body_text=draft["draft_body"],
    )
    if result["success"]:
        db.execute(
            "UPDATE reply_draft SET status='sent', sent_at=datetime('now','localtime'), "
            "updated_at=datetime('now','localtime') WHERE id=?",
            (draft_id,),
        )
    else:
        db.execute(
            "UPDATE reply_draft SET status='send_failed', "
            "updated_at=datetime('now','localtime') WHERE id=?",
            (draft_id,),
        )
    db.commit()
    return JSONResponse({"status": "sent" if result["success"] else "failed", "error": result.get("error")})


@router.post("/api/replies/{draft_id}/ignore")
def ignore_reply(
    draft_id: int,
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    """Mark a reply draft as ignored."""
    db = get_db()
    db.execute(
        "UPDATE reply_draft SET status='cancelled', updated_at=datetime('now','localtime') WHERE id=?",
        (draft_id,),
    )
    db.commit()
    return JSONResponse({"status": "ok"})


def _parse_ids(raw: str) -> list[int]:
    if not raw or not raw.strip():
        return []
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids
