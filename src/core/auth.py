"""Shared HTTP Basic auth dependency for all agents."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from src.core.config import PlatformConfig, get_config

security = HTTPBasic(auto_error=False)


def require_auth(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
    config: Annotated[PlatformConfig, Depends(get_config)],
) -> None:
    if not config.basic_user or not config.basic_password:
        return
    if credentials is None:
        raise HTTPException(
            status_code=401,
            detail="需要登录",
            headers={"WWW-Authenticate": "Basic"},
        )
    if (
        credentials.username != config.basic_user
        or credentials.password != config.basic_password
    ):
        raise HTTPException(
            status_code=401,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Basic"},
        )
