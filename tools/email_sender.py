"""Send emails via SMTP using Python stdlib."""

from __future__ import annotations

import logging
import smtplib
import time
import uuid
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from typing import Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class SmtpConfig:
    host: str = "localhost"
    port: int = 587
    username: str = ""
    password: str = ""
    from_email: str = ""
    from_name: str = "外贸团队"
    reply_to_email: str = ""
    use_tls: bool = True
    use_ssl: bool = False


def _create_connection(config: SmtpConfig) -> smtplib.SMTP:
    if config.use_ssl:
        conn = smtplib.SMTP_SSL(config.host, config.port, timeout=30)
    else:
        conn = smtplib.SMTP(config.host, config.port, timeout=30)
        if config.use_tls:
            try:
                conn.starttls()
            except Exception:
                logger.warning("SMTP server %s does not support STARTTLS, continuing in plain text", config.host)
    if config.username and config.password:
        conn.login(config.username, config.password)
    return conn


def send_single_email(
    config: SmtpConfig,
    to_email: str,
    to_name: str = "",
    subject: str = "",
    body_text: str = "",
    body_html: str = "",
) -> dict[str, Any]:
    """Send a single email. Returns result dict with success/error."""
    to_email = (to_email or "").strip()
    if not config.host or not config.from_email:
        return {"success": False, "error": "SMTP 未配置", "message_id": None}

    if not to_email:
        return {"success": False, "error": "收件人邮箱为空", "message_id": None}

    # Build message
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{config.from_name} <{config.from_email}>"
    msg["To"] = f"{to_name} <{to_email}>" if to_name else to_email
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    domain = config.from_email.split("@")[-1] if "@" in (config.from_email or "") else "local"
    msg["Message-ID"] = f"<{uuid.uuid4()}@{domain}>"
    reply_to = config.reply_to_email or config.from_email
    if reply_to:
        msg["Reply-To"] = reply_to
        msg["List-Unsubscribe"] = f"<mailto:{reply_to}?subject=unsubscribe>"

    msg.attach(MIMEText(body_text or "", "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        conn = _create_connection(config)
        conn.sendmail(config.from_email, [to_email], msg.as_string())
        conn.quit()
        logger.info("Email sent to %s: %s", to_email, subject)
        return {"success": True, "error": None, "message_id": None}
    except Exception as e:
        logger.error("Failed to send to %s: %s", to_email, e)
        return {"success": False, "error": str(e), "message_id": None}


def send_emails_batch(
    config: SmtpConfig,
    emails: list[dict[str, Any]],
    *,
    delay_seconds: float = 45.0,
    progress_callback: Callable | None = None,
) -> list[dict[str, Any]]:
    """Send multiple emails with configurable delay."""
    results = []
    total = len(emails)
    for i, item in enumerate(emails):
        if item.get("skip"):
            results.append({**item, "send_success": None, "send_error": "已跳过"})
            continue

        if progress_callback:
            progress_callback({
                "phase": "send",
                "current": i + 1,
                "total": total,
                "message": f"发送 {i + 1}/{total}: {item.get('company_name', '')}",
            })

        result = send_single_email(
            config=config,
            to_email=str(item.get("contact_email", "")),
            to_name=str(item.get("contact_name", "")),
            subject=str(item.get("subject", "")),
            body_text=str(item.get("body_text", "")),
            body_html=str(item.get("body_html", "")),
        )
        results.append({
            **item,
            "send_success": result["success"],
            "send_error": result["error"],
        })

        if i < total - 1 and delay_seconds > 0:
            time.sleep(delay_seconds)

    return results
