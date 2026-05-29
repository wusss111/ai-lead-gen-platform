"""Session-based auth with Redis. Provides require_auth and require_admin deps."""

from __future__ import annotations

import json
import logging
import secrets
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request
from redis import Redis

from src.core.config import PlatformConfig, get_config

logger = logging.getLogger(__name__)

SESSION_TTL = 86400  # 24 hours


def _redis(config: PlatformConfig) -> Redis:
    return Redis.from_url(config.redis_url)


def _get_session(request: Request, config: PlatformConfig) -> dict | None:
    """Read session_id from cookie, fetch user from Redis. Returns None if not logged in."""
    session_id = request.cookies.get("session_id")
    if not session_id:
        return None
    try:
        r = _redis(config)
        data = r.get(f"session:{session_id}")
        if data is None:
            return None
        user = json.loads(data if isinstance(data, str) else data.decode("utf-8"))
        # Refresh TTL
        r.expire(f"session:{session_id}", SESSION_TTL)
        return user
    except Exception:
        logger.exception("Session read error")
        return None


def create_session(config: PlatformConfig, user: dict) -> str:
    """Create a Redis session and return session_id. Caller is responsible for Set-Cookie."""
    session_id = secrets.token_urlsafe(32)
    r = _redis(config)
    r.setex(f"session:{session_id}", SESSION_TTL, json.dumps(user, ensure_ascii=False))
    return session_id


def destroy_session(config: PlatformConfig, session_id: str) -> None:
    """Delete a Redis session."""
    try:
        r = _redis(config)
        r.delete(f"session:{session_id}")
    except Exception:
        logger.exception("Session delete error")


def get_current_user(
    request: Request,
    config: Annotated[PlatformConfig, Depends(get_config)],
) -> dict | None:
    """从 Cookie 读取当前用户。未登录返回 None（不抛异常）。"""
    return _get_session(request, config)


def require_auth(
    request: Request,
    config: Annotated[PlatformConfig, Depends(get_config)],
) -> dict:
    """需要登录。返回 user dict {id, name, role}。未登录抛 401。"""
    user = _get_session(request, config)
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_admin(
    user: Annotated[dict, Depends(require_auth)],
) -> dict:
    """需要管理员角色。非 admin 抛 403。"""
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可访问")
    return user


def apply_sales_filter(where: list, params: list, user: dict, table_alias: str = "c") -> None:
    """销售只能看到分配给自己的客户，管理员看到全部。"""
    if user.get("role") == "salesperson":
        where.append(f"{table_alias}.assigned_salesperson_id = ?")
        params.append(user["id"])
