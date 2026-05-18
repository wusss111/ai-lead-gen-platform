"""RQ tasks for email generation and sending."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

# Ensure .env is loaded for worker processes
from dotenv import load_dotenv as _load_dotenv
_env_path = Path(__file__).resolve().parent.parent.parent.parent / ".env"
if _env_path.is_file():
    _load_dotenv(_env_path)

logger = logging.getLogger(__name__)


def generate_emails_job(
    folder_job_id: str,
    data_root: str,
    *,
    customer_ids: list[int] | None = None,
    language: str = "auto",
    from_name: str = "外贸团队",
    from_company: str = "",
    model: str | None = None,
) -> dict[str, Any]:
    """Generate inquiry emails for selected customers from the database."""
    from rq import get_current_job
    from src.core.database import get_db, dicts_from_rows
    from tools.email_generator import generate_emails_batch

    db = get_db()

    if customer_ids:
        placeholders = ",".join("?" for _ in customer_ids)
        rows = dicts_from_rows(
            db.execute(
                f"SELECT * FROM customer WHERE id IN ({placeholders}) ORDER BY overall_score_computed DESC",
                customer_ids,
            ).fetchall()
        )
    else:
        rows = dicts_from_rows(
            db.execute(
                "SELECT * FROM customer WHERE contact_email IS NOT NULL AND contact_email != '' "
                "AND (deal_recommendation IN ('high_intent','watch') OR deal_recommendation IS NULL) "
                "ORDER BY overall_score_computed DESC LIMIT 50"
            ).fetchall()
        )

    if not rows:
        return {"generated": 0, "skipped": 0, "emails": []}

    def rq_progress(payload: dict[str, Any]) -> None:
        job = get_current_job()
        if job is None:
            return
        try:
            job.meta["progress"] = payload
            job.save_meta()
        except Exception:
            pass

    emails = generate_emails_batch(
        rows,
        from_name=from_name,
        from_company=from_company,
        language=language,
        model=model,
        progress_callback=rq_progress,
    )

    # Save to file for API access
    root = Path(data_root)
    mail_dir = root / "jobs" / folder_job_id
    mail_dir.mkdir(parents=True, exist_ok=True)
    (mail_dir / "emails.json").write_text(
        json.dumps(emails, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Update DB with generated email status
    for e in emails:
        if e.get("customer_id"):
            db.execute(
                "UPDATE customer SET email_subject=?, email_body=?, email_status='generated', updated_at=datetime('now','localtime') WHERE id=?",
                (e.get("subject", ""), e.get("body_text", ""), e["customer_id"]),
            )
    db.commit()

    generated = sum(1 for e in emails if not e.get("skip"))
    skipped = sum(1 for e in emails if e.get("skip"))

    return {
        "generated": generated,
        "skipped": skipped,
        "total": len(emails),
        "emails": emails,
    }


def _get_today_send_count(db) -> int:
    """Return how many emails were sent today."""
    row = db.execute(
        "SELECT COUNT(1) FROM daily_send_log WHERE sent_date = date('now','localtime') AND status = 'sent'"
    ).fetchone()
    return row[0] if row else 0


def send_emails_job(
    folder_job_id: str,
    data_root: str,
    *,
    smtp_config_dict: dict[str, Any],
    selected_ids: list[int] | None = None,
    daily_limit: int = 50,
    respect_timezone: bool = True,
    business_hours_start: int = 9,
    business_hours_end: int = 17,
) -> dict[str, Any]:
    """Send generated emails via Gmail API (preferred) or SMTP (fallback)."""
    from rq import get_current_job
    from src.core.database import get_db

    root = Path(data_root)
    mail_dir = root / "jobs" / folder_job_id
    emails_path = mail_dir / "emails.json"

    if not emails_path.is_file():
        raise FileNotFoundError("No emails.json found. Generate emails first.")

    emails = json.loads(emails_path.read_text(encoding="utf-8"))

    # Filter: skip already-sent and skipped
    to_send = []
    for e in emails:
        if e.get("skip"):
            continue
        if e.get("send_success"):
            continue
        if selected_ids is not None and e.get("customer_id") not in selected_ids:
            continue
        to_send.append(e)

    if not to_send:
        return {"sent": 0, "failed": 0, "skipped": len(emails) - len(to_send)}

    # Daily quota check
    db = get_db()
    sent_today = _get_today_send_count(db)
    remaining = daily_limit - sent_today
    if remaining <= 0:
        logger.warning("Daily limit (%d) reached. %d already sent today.", daily_limit, sent_today)
        return {"sent": 0, "failed": 0, "skipped": len(emails), "error": f"Daily limit ({daily_limit}) reached"}
    if len(to_send) > remaining:
        logger.info("Trimming batch from %d to %d (daily limit %d, %d already sent)", len(to_send), remaining, daily_limit, sent_today)
        to_send = to_send[:remaining]

    # Timezone-aware filtering: only send to customers currently in business hours
    skipped_tz_count = 0
    if respect_timezone:
        from tools.country_timezone import get_utc_offset, local_hour_now
        filtered = []
        skipped_tz = []
        for e in to_send:
            country = e.get("country_region", "")
            offset = get_utc_offset(country)
            if offset is None:
                filtered.append(e)  # unknown timezone, send anyway
            else:
                local_h = local_hour_now(offset)
                if business_hours_start <= local_h < business_hours_end:
                    filtered.append(e)
                else:
                    skipped_tz.append(e.get("company_name", "")[:30])
        if skipped_tz:
            logger.info(
                "Timezone filter: skipped %d emails outside %02d:00-%02d:00 local. Sampled: %s",
                len(skipped_tz), business_hours_start, business_hours_end, skipped_tz[:5],
            )
        skipped_tz_count = len(skipped_tz)
        to_send = filtered

    if not to_send:
        msg = f"No recipients currently in business hours ({business_hours_start:02d}:00-{business_hours_end:02d}:00). {skipped_tz_count} skipped."
        logger.info(msg)
        return {"sent": 0, "failed": 0, "skipped": skipped_tz_count, "error": msg}

    # Sort by UTC offset: send to earlier timezones first
    if respect_timezone:
        from tools.country_timezone import get_utc_offset
        to_send.sort(key=lambda e: get_utc_offset(e.get("country_region", "")) or 0)

    def rq_progress(payload: dict[str, Any]) -> None:
        job = get_current_job()
        if job is None:
            return
        try:
            job.meta["progress"] = payload
            job.save_meta()
        except Exception:
            pass

    send_delay = float(smtp_config_dict.get("send_delay_seconds", 45.0))
    from_email = str(smtp_config_dict.get("from_email", ""))

    # 清理 email 地址中的换行符
    for e in to_send:
        if "contact_email" in e and isinstance(e["contact_email"], str):
            e["contact_email"] = e["contact_email"].strip()

    # 注入追踪像素
    from src.core.tracking_pixel import generate_tracking_id, inject_tracking_pixel
    for e in to_send:
        tid = generate_tracking_id()
        e["tracking_id"] = tid
        if e.get("body_html"):
            e["body_html"] = inject_tracking_pixel(e["body_html"], tid)

    # Auto-detect: use Gmail API only if OAuth token exists (fully set up)
    _repo_root = Path(__file__).resolve().parent.parent.parent.parent
    _gmail_token = _repo_root / "var" / "gmail_token.json"
    _gmail_secret = _repo_root / "var" / "gmail_client_secret.json"

    if _gmail_token.is_file():
        logger.info("Using Gmail API for %d emails (delay=%ds)", len(to_send), send_delay)
        from tools.gmail_sender import send_emails_batch as send_batch
        results = send_batch(to_send, delay_seconds=send_delay, from_email=from_email, progress_callback=rq_progress)
    elif _gmail_secret.is_file():
        logger.warning(
            "Gmail client secret exists but OAuth token not found. "
            "Run: python tools/setup_gmail_oauth.py  first, then retry. "
            "Falling back to SMTP."
        )
        from tools.email_sender import SmtpConfig, send_emails_batch as send_batch
        _smtp_fields = {"host","port","username","password","from_email","from_name","reply_to_email","use_tls","use_ssl"}
        cfg = SmtpConfig(**{k: v for k, v in smtp_config_dict.items() if k in _smtp_fields})
        results = send_batch(cfg, to_send, delay_seconds=send_delay, progress_callback=rq_progress)
    else:
        logger.info("Using SMTP for %d emails (delay=%ds)", len(to_send), send_delay)
        from tools.email_sender import SmtpConfig, send_emails_batch as send_batch
        _smtp_fields = {"host","port","username","password","from_email","from_name","reply_to_email","use_tls","use_ssl"}
        cfg = SmtpConfig(**{k: v for k, v in smtp_config_dict.items() if k in _smtp_fields})
        results = send_batch(cfg, to_send, delay_seconds=send_delay, progress_callback=rq_progress)

    # Update emails.json and DB with send results
    email_map = {e.get("customer_id"): i for i, e in enumerate(emails)}
    for r in results:
        cid = r.get("customer_id")
        if cid and cid in email_map:
            emails[email_map[cid]] = r
        if cid and r.get("send_success"):
            db.execute(
                "UPDATE customer SET email_status='sent', email_sent_at=datetime('now','localtime'), updated_at=datetime('now','localtime') WHERE id=?",
                (cid,),
            )
            db.execute(
                "INSERT INTO daily_send_log (sent_date, recipient_email, customer_id, status, tracking_id, salesperson_id) "
                "VALUES (date('now','localtime'), ?, ?, 'sent', ?, "
                "(SELECT assigned_salesperson_id FROM customer WHERE id=?))",
                (r.get("contact_email", ""), cid, r.get("tracking_id", ""), cid),
            )
        elif cid and not r.get("send_success"):
            db.execute(
                "UPDATE customer SET email_status='failed', updated_at=datetime('now','localtime') WHERE id=?",
                (cid,),
            )
            db.execute(
                "INSERT INTO daily_send_log (sent_date, recipient_email, customer_id, status, tracking_id, salesperson_id) "
                "VALUES (date('now','localtime'), ?, ?, 'failed', ?, "
                "(SELECT assigned_salesperson_id FROM customer WHERE id=?))",
                (r.get("contact_email", ""), cid, r.get("tracking_id", ""), cid),
            )
    db.commit()

    (mail_dir / "emails.json").write_text(
        json.dumps(emails, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    sent = sum(1 for r in results if r.get("send_success"))
    failed = sum(1 for r in results if not r.get("send_success"))

    return {"sent": sent, "failed": failed, "total": len(results), "emails": emails}
