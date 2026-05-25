"""IMAP inbox monitoring — detect customer replies to sent emails."""
from __future__ import annotations

import email
import imaplib
import logging
import re
import time
from dataclasses import dataclass
from email.header import decode_header
from email.message import Message
from typing import Any

logger = logging.getLogger(__name__)

_MESSAGE_ID_RE = re.compile(r'<([^>]+)>')


@dataclass
class ImapConfig:
    host: str = ""
    port: int = 993
    username: str = ""
    password: str = ""


def parse_message_id(header_value: str | None) -> str | None:
    """Extract message-id from an email header value."""
    if not header_value:
        return None
    m = _MESSAGE_ID_RE.search(header_value)
    return m.group(1) if m else header_value.strip()


def _decode_mime_words(raw: str) -> str:
    """Decode RFC 2047 encoded header to readable string."""
    if not raw:
        return ""
    parts = decode_header(raw)
    result = ""
    for part_bytes, charset in parts:
        if isinstance(part_bytes, bytes):
            result += part_bytes.decode(charset or "utf-8", errors="replace")
        else:
            result += str(part_bytes) if part_bytes else ""
    return result


def _get_plain_text(msg: Message) -> str:
    """Walk a multipart message and extract text/plain body."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode("utf-8", errors="replace")
    return ""


def connect_imap(cfg: ImapConfig) -> imaplib.IMAP4_SSL | None:
    """Connect to IMAP server and login."""
    try:
        conn = imaplib.IMAP4_SSL(cfg.host, cfg.port, timeout=15)
        conn.login(cfg.username, cfg.password)
        return conn
    except Exception as e:
        logger.warning("IMAP connect failed for %s@%s: %s", cfg.username, cfg.host, e)
        return None


def detect_replies(conn: imaplib.IMAP4_SSL, known_message_ids: set[str],
                   days_back: int = 7) -> list[dict[str, Any]]:
    """
    Search INBOX for replies to our sent messages.
    Returns list of reply dicts with {subject, body, message_id, in_reply_to, from_addr, date}.
    """
    results: list[dict[str, Any]] = []
    try:
        conn.select("INBOX", readonly=True)
        since = time.strftime("%d-%b-%Y", time.localtime(time.time() - days_back * 86400))
        status, data = conn.search(None, f'(UNSEEN SINCE "{since}")')
        if status != "OK":
            return results

        msg_ids = data[0].split()
        for num in msg_ids:
            try:
                status, raw = conn.fetch(num, "(RFC822)")
                if status != "OK":
                    continue
                msg_bytes = raw[0][1]
                msg = email.message_from_bytes(msg_bytes)
                in_reply_to = msg.get("In-Reply-To", "") or msg.get("References", "")
                if not in_reply_to:
                    continue
                ref_ids = _extract_all_message_ids(in_reply_to)
                if ref_ids & known_message_ids:
                    results.append({
                        "subject": _decode_mime_words(msg.get("Subject", "")),
                        "body": _get_plain_text(msg),
                        "message_id": parse_message_id(msg.get("Message-ID", "")),
                        "in_reply_to": parse_message_id(in_reply_to),
                        "from_addr": _decode_mime_words(msg.get("From", "")),
                        "date": msg.get("Date", ""),
                    })
            except Exception as exc:
                logger.warning("Error parsing message %s: %s", num, exc)
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return results


def _extract_all_message_ids(header_value: str) -> set[str]:
    """Extract all message-ids from headers like References (can contain multiple)."""
    return set(_MESSAGE_ID_RE.findall(header_value))


def poll_all_salespersons(data_dir: str) -> dict[int, list[dict]]:
    """
    Poll all active salespersons' IMAP inboxes for replies.
    Returns dict: {salesperson_id: [reply_dict, ...]}
    """
    from src.core.database import get_db

    db = get_db()
    rows = db.execute(
        "SELECT id, imap_host, imap_port, smtp_username, smtp_password "
        "FROM salesperson WHERE is_active=1 AND imap_host != '' AND smtp_username != ''"
    ).fetchall()
    if not rows:
        return {}

    # Collect all known sent message-ids
    known_ids: set[str] = set()
    sent_logs = db.execute(
        "SELECT DISTINCT tracking_id FROM daily_send_log WHERE tracking_id IS NOT NULL AND tracking_id != ''"
    ).fetchall()
    known_ids.update(r["tracking_id"] for r in sent_logs)

    all_replies: dict[int, list[dict]] = {}
    for sp in rows:
        cfg = ImapConfig(host=sp["imap_host"], port=sp["imap_port"] or 993,
                         username=sp["smtp_username"], password=sp["smtp_password"])
        conn = connect_imap(cfg)
        if not conn:
            continue
        replies = detect_replies(conn, known_ids)
        if replies:
            all_replies[sp["id"]] = replies
            logger.info("Found %d replies for salesperson %d", len(replies), sp["id"])

    return all_replies
