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
_TOKEN_DIR = _REPO_ROOT / "var" / "gmail_tokens"
_CLIENT_SECRET_PATH = _REPO_ROOT / "var" / "gmail_client_secret.json"
# Legacy: single global token
_GLOBAL_TOKEN_PATH = _REPO_ROOT / "var" / "gmail_token.json"


def _token_path_for(salesperson_id: int | None) -> Path:
    """Get the token file path for a specific salesperson, or global fallback."""
    if salesperson_id is not None:
        return _TOKEN_DIR / f"gmail_token_{salesperson_id}.json"
    return _GLOBAL_TOKEN_PATH


def _get_creds(salesperson_id: int | None = None) -> Credentials:
    """Get valid Gmail API credentials, running OAuth flow if needed.

    Args:
        salesperson_id: If provided, load token for that salesperson.
                        If None, use global token.
    """
    token_path = _token_path_for(salesperson_id)
    creds = None
    if token_path.is_file():
        creds = Credentials.from_authorized_user_info(
            json.loads(token_path.read_text(encoding="utf-8")), SCOPES
        )
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")
        else:
            # Can't auto-renew without interactive auth
            return creds  # Will fail on use, caller must handle
    return creds


def has_token(salesperson_id: int) -> bool:
    """Check if a valid Gmail API token exists for the given salesperson."""
    token_path = _token_path_for(salesperson_id)
    if not token_path.is_file():
        return False
    try:
        creds = _get_creds(salesperson_id)
        return creds is not None and creds.valid
    except Exception:
        return False


def run_oauth_for_salesperson(salesperson_id: int) -> dict[str, Any]:
    """Run interactive OAuth flow for a salesperson. Returns {success, email}."""
    if not _CLIENT_SECRET_PATH.is_file():
        return {"success": False, "error": "Gmail client secret 未配置，请联系管理员"}

    flow = InstalledAppFlow.from_client_secrets_file(
        str(_CLIENT_SECRET_PATH), SCOPES
    )
    creds = flow.run_local_server(
        port=0,
        authorization_prompt_message="请使用业务员自己的 Gmail 邮箱登录授权",
        success_message="Gmail 授权成功！可以关闭此页面。",
    )

    token_path = _token_path_for(salesperson_id)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json(), encoding="utf-8")

    # Get email from credentials
    try:
        from googleapiclient.discovery import build
        service = build("gmail", "v1", credentials=creds)
        profile = service.users().getProfile(userId="me").execute()
        email = profile.get("emailAddress", "")
    except Exception:
        email = ""

    logger.info("Gmail OAuth completed for salesperson %d: %s", salesperson_id, email)
    return {"success": True, "email": email}


def send_single_email(
    to_email: str,
    subject: str,
    body_text: str,
    *,
    body_html: str = "",
    from_email: str = "",
    salesperson_id: int | None = None,
) -> dict[str, Any]:
    """Send a single email via Gmail API. Uses per-salesperson token if provided."""
    to_email = to_email.strip()
    if not to_email:
        return {"success": False, "error": "收件人邮箱为空"}

    try:
        creds = _get_creds(salesperson_id)
        if creds is None or not creds.valid:
            return {"success": False, "error": "Gmail 授权已过期，请重新授权"}
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


def get_all_salesperson_token_ids() -> set[int]:
    """Return set of salesperson IDs that have valid Gmail tokens."""
    valid_ids: set[int] = set()
    if not _TOKEN_DIR.is_dir():
        return valid_ids
    for token_file in _TOKEN_DIR.glob("gmail_token_*.json"):
        try:
            sid = int(token_file.stem.replace("gmail_token_", ""))
            valid_ids.add(sid)
        except ValueError:
            pass
    return valid_ids


def send_emails_batch(
    emails: list[dict[str, Any]],
    *,
    delay_seconds: float = 45.0,
    from_email: str = "",
    progress_callback: Callable | None = None,
    get_salesperson_id: Callable[[dict[str, Any]], int | None] | None = None,
) -> list[dict[str, Any]]:
    """Send multiple emails via Gmail API.

    Args:
        get_salesperson_id: Optional callback that takes an email item dict
                           and returns the salesperson_id to use for that email.
    """
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

        sp_id = get_salesperson_id(item) if get_salesperson_id else None
        r = send_single_email(
            to_email=str(item.get("contact_email", "")),
            subject=str(item.get("subject", "")),
            body_text=str(item.get("body_text", "")),
            body_html=str(item.get("body_html", "")),
            from_email=from_email,
            salesperson_id=sp_id,
        )
        results.append({**item, "send_success": r["success"], "send_error": r["error"]})

        if i < total - 1 and delay_seconds > 0:
            time.sleep(delay_seconds)

    return results
