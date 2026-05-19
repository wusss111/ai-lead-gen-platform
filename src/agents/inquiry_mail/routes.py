"""Inquiry mail API and page routes."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from src.core.auth import require_auth
from src.core.config import PlatformConfig, get_config
from src.core.redis_utils import get_queue, get_rq_job_info
from src.core.database import get_db, dicts_from_rows
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
    limit: int = 50,
) -> JSONResponse:
    """List customers that can receive inquiry emails."""
    db = get_db()
    where = [
        "c.contact_email IS NOT NULL",
        "c.contact_email != ''",
    ]
    params: list[Any] = []
    if search.strip():
        where.append("(c.company_name LIKE ? OR c.contact_email LIKE ?)")
        kw = f"%{search.strip()}%"
        params.extend([kw, kw])
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
    where_clause = " AND ".join(where)
    rows = db.execute(
        f"SELECT c.id, c.company_name, c.contact_name, c.contact_email, c.country_region, "
        f"c.deal_recommendation, c.overall_score_computed, c.email_status, "
        f"c.assigned_salesperson_id, COALESCE(s.name, '') as salesperson_name, "
        f"c.tracking_last_opened_at "
        f"FROM customer c "
        f"LEFT JOIN salesperson s ON c.assigned_salesperson_id = s.id "
        f"WHERE {where_clause} ORDER BY c.overall_score_computed DESC LIMIT ?",
        params + [limit],
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

    # 检查是否有可用的发送通道：Gmail API token 或 SMTP
    _gmail_token = Path(__file__).resolve().parent.parent.parent.parent / "var" / "gmail_token.json"
    _has_gmail = _gmail_token.is_file()
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


def _parse_ids(raw: str) -> list[int]:
    if not raw or not raw.strip():
        return []
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.append(int(part))
    return ids
