"""Inquiry mail agent configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class InquiryMailConfig:
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    from_email: str = ""
    from_name: str = "外贸团队"
    use_tls: bool = True
    use_ssl: bool = False
    send_delay_seconds: float = 45.0
    max_per_job: int = 50
    daily_limit: int = 50
    default_language: str = "auto"
    reply_to_email: str = ""
    respect_timezone: bool = True
    business_hours_start: int = 9
    business_hours_end: int = 17
    queue_name: str = "inquiry_mail:default"
    imap_poll_interval: int = 60
    wework_corp_id: str = ""
    wework_agent_id: str = ""
    wework_agent_secret: str = ""

    @classmethod
    def from_env(cls) -> "InquiryMailConfig":
        return cls(
            smtp_host=(os.environ.get("SMTP_HOST") or "").strip(),
            smtp_port=int(os.environ.get("SMTP_PORT") or "587"),
            smtp_username=(os.environ.get("SMTP_USER") or "").strip(),
            smtp_password=(os.environ.get("SMTP_PASSWORD") or "").strip(),
            from_email=(os.environ.get("SMTP_FROM_EMAIL") or "").strip(),
            from_name=(os.environ.get("SMTP_FROM_NAME") or "外贸团队").strip(),
            reply_to_email=(os.environ.get("SMTP_REPLY_TO") or "").strip(),
            use_tls=(os.environ.get("SMTP_USE_TLS") or "true").lower() != "false",
            use_ssl=(os.environ.get("SMTP_USE_SSL") or "false").lower() == "true",
            send_delay_seconds=float(os.environ.get("SMTP_SEND_DELAY") or "45"),
            max_per_job=int(os.environ.get("MAIL_MAX_PER_JOB") or "50"),
            daily_limit=int(os.environ.get("MAIL_DAILY_LIMIT") or "50"),
            default_language=(os.environ.get("MAIL_DEFAULT_LANG") or "auto").strip(),
            respect_timezone=(os.environ.get("MAIL_RESPECT_TZ") or "true").lower() != "false",
            business_hours_start=int(os.environ.get("MAIL_SEND_HOUR_START") or "9"),
            business_hours_end=int(os.environ.get("MAIL_SEND_HOUR_END") or "17"),
            imap_poll_interval=int(os.environ.get("IMAP_POLL_INTERVAL") or "60"),
            wework_corp_id=(os.environ.get("WECOM_CORP_ID") or "").strip(),
            wework_agent_id=(os.environ.get("WECOM_AGENT_ID") or "").strip(),
            wework_agent_secret=(os.environ.get("WECOM_AGENT_SECRET") or "").strip(),
        )
