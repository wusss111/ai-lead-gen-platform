"""WeChat Work (企业微信) integration — template card messaging."""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

WECOM_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
WECOM_CARD_URL = "https://qyapi.weixin.qq.com/cgi-bin/message/send"


@dataclass
class WeComConfig:
    corp_id: str = ""
    agent_id: str = ""
    agent_secret: str = ""

    @classmethod
    def from_env(cls) -> "WeComConfig":
        import os
        return cls(
            corp_id=(os.environ.get("WECOM_CORP_ID") or "").strip(),
            agent_id=(os.environ.get("WECOM_AGENT_ID") or "").strip(),
            agent_secret=(os.environ.get("WECOM_AGENT_SECRET") or "").strip(),
        )


def _get_access_token(cfg: WeComConfig) -> str | None:
    """Get WeCom API access token (cached in memory, expires 2h)."""
    url = f"{WECOM_TOKEN_URL}?corpid={cfg.corp_id}&corpsecret={cfg.agent_secret}"
    try:
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data.get("errcode") == 0:
            return data["access_token"]
        logger.error("WeCom token error: %s", data)
        return None
    except Exception as e:
        logger.error("WeCom token request failed: %s", e)
        return None


def send_reply_card(
    *,
    wework_userid: str,
    customer_name: str,
    original_snippet: str,
    draft_snippet: str,
    draft_id: int,
    base_url: str = "",
    cfg: WeComConfig | None = None,
) -> bool:
    """
    Send a template card notification to a specific WeCom user.

    The card contains: customer name, original email snippet, AI draft snippet,
    and three action buttons (Confirm Send / Edit / Ignore).
    """
    if cfg is None:
        cfg = WeComConfig.from_env()

    if not cfg.corp_id or not cfg.agent_id or not cfg.agent_secret:
        logger.warning("WeCom not configured, skipping notification")
        return False

    token = _get_access_token(cfg)
    if not token:
        return False

    if not base_url:
        import os
        base_url = (os.environ.get("PLATFORM_BASE_URL") or "http://localhost:8000").rstrip("/")

    # Build task_id as callback data carrier
    task_id = json.dumps({"draft_id": draft_id, "ts": int(time.time())})

    payload = {
        "touser": wework_userid,
        "msgtype": "template_card",
        "agentid": int(cfg.agent_id),
        "template_card": {
            "card_type": "text_notice",
            "source": {"desc": "询盘回信提醒", "desc_color": 1},
            "main_title": {"title": f"{customer_name} 回复了你的邮件"},
            "emphasis_content": {"title": original_snippet[:100], "desc": "客户原文"},
            "sub_title_text": f"AI 建议回复：{draft_snippet[:200]}",
            "horizontal_content_list": [
                {"keyname": "客户", "value": customer_name},
                {"keyname": "时间", "value": time.strftime("%H:%M")},
            ],
            "card_action": {
                "type": 1,
                "url": f"{base_url}/inquiry-mail/reply/{draft_id}",
            },
            "task_id": task_id,
            "button_list": [
                {"text": "确认发送", "style": 1, "key": "confirm_send"},
                {"text": "编辑", "style": 2, "key": "edit"},
                {"text": "忽略", "style": 0, "key": "ignore"},
            ],
        },
    }

    url = f"{WECOM_CARD_URL}?access_token={token}"
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read())
        if data.get("errcode") == 0:
            logger.info("WeCom card sent to %s, draft_id=%d", wework_userid, draft_id)
            return True
        logger.error("WeCom card send failed: %s", data)
        return False
    except Exception as e:
        logger.error("WeCom card request failed: %s", e)
        return False
