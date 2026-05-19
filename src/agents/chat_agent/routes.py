"""Chat agent API routes."""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from src.core.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


@router.post("/api/chat/send")
async def chat_send(
    request: Request,
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    """发送消息，返回 AI 回复（非流式，兼容旧版）。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求体不是有效 JSON"}, status_code=400)

    user_message = (body.get("message") or "").strip()
    history = body.get("history") or []

    if not user_message:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    from src.agents.chat_agent.agent_loop import run_agent
    result = run_agent(user_message, history)

    return JSONResponse({
        "reply": result["reply"],
        "confirm": result.get("confirm"),
        "tool_calls": result.get("tool_calls", []),
    })


@router.post("/api/chat/stream")
async def chat_stream(
    request: Request,
    _: Annotated[None, Depends(require_auth)],
):
    """流式发送消息，返回 SSE 事件流（支持思维链可视化）。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求体不是有效 JSON"}, status_code=400)

    user_message = (body.get("message") or "").strip()
    history = body.get("history") or []

    if not user_message:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    from src.agents.chat_agent.agent_loop import run_agent_stream

    async def event_stream():
        try:
            for event_type, data in run_agent_stream(user_message, history):
                yield f"event: {event_type}\ndata: {data}\n\n"
        except Exception as e:
            logger.exception("Stream error")
            yield f"event: error\ndata: {{\"message\":\"{str(e)[:100]}\"}}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/chat/confirm")
async def chat_confirm(
    request: Request,
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    """用户确认操作，执行实际动作。"""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "请求体不是有效 JSON"}, status_code=400)

    confirm = body.get("confirm") or {}

    from src.agents.chat_agent.agent_loop import execute_confirmed_action
    result = execute_confirmed_action(confirm)

    return JSONResponse(result)


@router.get("/api/chat/tools")
def list_tools(
    _: Annotated[None, Depends(require_auth)],
) -> JSONResponse:
    """返回可用工具列表（供调试）。"""
    from src.agents.chat_agent.tools import TOOL_DEFINITIONS
    return JSONResponse(TOOL_DEFINITIONS)
