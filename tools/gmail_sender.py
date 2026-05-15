"""Send emails via Gmail API (HTTPS, bypasses SMTP firewall blocking)."""

from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate
from pathlib import Path
from typing import Any, Callable

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
_REPO_ROOT = Path(__file__).resolve().parent.parent
_TOKEN_PATH = _REPO_ROOT / "var" / "gmail_token.json"
_CLIENT_SECRET_PATH = _REPO_ROOT / "var" / "gmail_client_secret.json"


def _get_creds() -> Credentials:
    """Get valid Gmail API credentials, running OAuth flow if needed."""
    creds = None
    if _TOKEN_PATH.is_file():
        creds = Credentials.from_authorized_user_info(
            json.loads(_TOKEN_PATH.read_text(encoding="utf-8")), SCOPES
        )
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not _CLIENT_SECRET_PATH.is_file():
                raise FileNotFoundError(
                    f"Gmail client secret not found at {_CLIENT_SECRET_PATH}. "
                    "Please save the OAuth JSON there."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(_CLIENT_SECRET_PATH), SCOPES
            )
            creds = flow.run_local_server(port=0)
        _TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return creds


def send_single_email(
    to_email: str,
    subject: str,
    body_text: str,
    *,
    body_html: str = "",
    from_email: str = "",
) -> dict[str, Any]:
    """Send a single email via Gmail API. Returns {success, error}."""
    to_email = to_email.strip()
    if not to_email:
        return {"success": False, "error": "收件人邮箱为空"}

    try:
        creds = _get_creds()
        service = build("gmail", "v1", credentials=creds)

        # Build RFC 2822 message
        if body_html:
            msg = MIMEMultipart("alternative")
            msg.attach(MIMEText(body_text or "", "plain", "utf-8"))
            msg.attach(MIMEText(body_html, "html", "utf-8"))
        else:
            msg = MIMEText(body_text or "", "plain", "utf-8")
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        domain = from_email.split("@")[-1] if "@" in from_email else "local"
        msg["Message-ID"] = f"<{uuid.uuid4()}@{domain}>"
        if from_email:
            msg["Reply-To"] = from_email
            msg["List-Unsubscribe"] = f"<mailto:{from_email}?subject=unsubscribe>"

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        sent = service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()

        logger.info("Gmail API sent to %s: %s", to_email, sent.get("id", "?"))
        return {"success": True, "error": None}

    except HttpError as e:
        logger.error("Gmail API error sending to %s: %s", to_email, e)
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error("Failed to send via Gmail API to %s: %s", to_email, e)
        return {"success": False, "error": str(e)}


def send_emails_batch(
    emails: list[dict[str, Any]],
    *,
    delay_seconds: float = 45.0,
    from_email: str = "",
    progress_callback: Callable | None = None,
) -> list[dict[str, Any]]:
    """Send multiple emails via Gmail API."""
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
                "message": f"Gmail API {i + 1}/{total}: {item.get('company_name', '')}",
            })

        r = send_single_email(
            to_email=str(item.get("contact_email", "")),
            subject=str(item.get("subject", "")),
            body_text=str(item.get("body_text", "")),
            body_html=str(item.get("body_html", "")),
            from_email=from_email,
        )
        results.append({**item, "send_success": r["success"], "send_error": r["error"]})

        if i < total - 1 and delay_seconds > 0:
            time.sleep(delay_seconds)

    return results
