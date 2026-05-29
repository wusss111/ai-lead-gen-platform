"""DeepSeek 官方 API（OpenAI 兼容）客户端封装。"""

from __future__ import annotations

import json
import logging
import os
import time as _time
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI

# ── Exception hierarchy ──

class DeepSeekError(RuntimeError):
    """Base exception for DeepSeek API errors."""

class DeepSeekCancelled(DeepSeekError):
    """Task was cancelled by user signal."""

class DeepSeekRetriesExhausted(DeepSeekError):
    """All retry attempts failed."""

class DeepSeekConnectionError(DeepSeekError):
    """Network or timeout error after retries."""

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.deepseek.com"
# 官方 V4 系列；需要更强推理时可设环境变量 DEEPSEEK_MODEL=deepseek-v4-pro
DEFAULT_MODEL = "deepseek-v4-flash"

_DOTENV_LOADED = False


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_dotenv_if_needed() -> None:
    """从仓库根目录 .env 加载环境变量（不覆盖已存在的环境变量）。"""
    global _DOTENV_LOADED
    if _DOTENV_LOADED:
        return
    _DOTENV_LOADED = True
    env_path = _repo_root() / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(env_path, override=False)


def default_model() -> str:
    _load_dotenv_if_needed()
    return (os.environ.get("DEEPSEEK_MODEL") or DEFAULT_MODEL).strip()


def make_client() -> OpenAI:
    _load_dotenv_if_needed()
    key = (os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not key:
        raise RuntimeError(
            "未设置 DEEPSEEK_API_KEY。请在 https://platform.deepseek.com/api_keys 创建密钥，"
            "任选其一：1) 复制 .env.example 为 .env 并填入密钥；2) PowerShell: "
            "$env:DEEPSEEK_API_KEY='sk-...'；3) 用户级持久: setx DEEPSEEK_API_KEY \"sk-...\"（新开终端生效）。"
        )
    base = (os.environ.get("DEEPSEEK_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    return OpenAI(
        api_key=key, base_url=base,
        timeout=httpx.Timeout(connect=10.0, read=90.0, write=30.0, pool=5.0),
    )


def chat_json(
    messages: list[dict[str, str]],
    *,
    model: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 8192,
    max_retries: int = 3,
) -> dict[str, Any]:
    """
    调用 Chat Completions，要求返回合法 JSON（需配合含「json」字样的 system/user 提示，见官方文档）。
    返回解析后的 dict；若重试耗尽或遇到不可重试错误，返回 {"_error": "..."}。
    """
    from openai import (
        APIConnectionError,
        APITimeoutError,
        APIStatusError,
    )

    last_error: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            client = make_client()
            m = model or default_model()
            resp = client.chat.completions.create(
                model=m,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
            raw = (resp.choices[0].message.content or "").strip()
            if not raw:
                raise RuntimeError("DeepSeek 返回空 content，可尝试增大 max_tokens 或缩短目录/证据长度。")
            return json.loads(raw)
        except (APIConnectionError, APITimeoutError) as e:
            last_error = e
            if attempt < max_retries:
                wait = 2 ** attempt  # 1s, 2s, 4s
                logger.warning(
                    "DeepSeek API 连接/超时错误 (尝试 %d/%d): %s，%d秒后重试...",
                    attempt + 1, max_retries + 1, e, wait,
                )
                _time.sleep(wait)
        except APIStatusError as e:
            if e.status_code == 429 or e.status_code >= 500:
                last_error = e
                if attempt < max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        "DeepSeek API HTTP %d (尝试 %d/%d): %s，%d秒后重试...",
                        e.status_code, attempt + 1, max_retries + 1, e, wait,
                    )
                    _time.sleep(wait)
            else:
                logger.error("DeepSeek API HTTP %d 不可重试: %s", e.status_code, e)
                return {"_error": str(e)}
        except Exception as e:
            logger.error("DeepSeek API 调用失败: %s", e)
            return {"_error": str(e)}

    logger.error("DeepSeek API 重试耗尽 (共%d次尝试): %s", max_retries + 1, last_error)
    return {"_error": str(last_error)}
