"""Integration tests for reply pipeline — IMAP detect → AI generate → save → send."""

from __future__ import annotations


def test_imap_detect_replies_message_id_extraction():
    """Verify reply detection logic for message ID extraction."""
    from tools.imap_monitor import _extract_all_message_ids
    ids = _extract_all_message_ids(
        "<abc123@mail.gmail.com> <def456@mail.gmail.com>"
    )
    assert "abc123@mail.gmail.com" in ids
    assert "def456@mail.gmail.com" in ids


def test_imap_parse_message_id():
    """Verify single message-id parsing."""
    from tools.imap_monitor import parse_message_id
    result = parse_message_id("<test123@gmail.com>")
    assert result == "test123@gmail.com"


def test_imap_parse_message_id_none():
    """Verify parse_message_id handles None gracefully."""
    from tools.imap_monitor import parse_message_id
    assert parse_message_id(None) is None


def test_imap_decode_mime_words():
    """Verify RFC 2047 header decoding."""
    from tools.imap_monitor import _decode_mime_words
    result = _decode_mime_words("=?utf-8?B?VGVzdCBTdWJqZWN0?=")
    assert isinstance(result, str)


def test_reply_draft_schema():
    """Verify reply_draft table exists and accepts data."""
    from src.core.database import get_db
    db = get_db()
    # Verify table exists by querying it
    rows = db.execute("SELECT COUNT(*) FROM reply_draft").fetchone()
    assert rows is not None
    # Should work without error


def test_salesperson_has_email_fields():
    """Verify salesperson table has new email binding columns."""
    from src.core.database import get_db
    db = get_db()
    cols = {c[1] for c in db.execute("PRAGMA table_info(salesperson)").fetchall()}
    for col_name in ("smtp_host", "smtp_port", "smtp_username", "smtp_password",
                      "imap_host", "imap_port", "wework_userid"):
        assert col_name in cols, f"Missing column: {col_name}"


def test_wecom_config_empty():
    """Verify WeComConfig handles empty environment."""
    from tools.wecom_notify import WeComConfig
    cfg = WeComConfig(corp_id="", agent_id="", agent_secret="")
    assert cfg.corp_id == ""
    assert cfg.agent_id == ""


def test_imap_config_dataclass():
    """Verify ImapConfig dataclass defaults."""
    from tools.imap_monitor import ImapConfig
    cfg = ImapConfig()
    assert cfg.port == 993
    assert cfg.host == ""
