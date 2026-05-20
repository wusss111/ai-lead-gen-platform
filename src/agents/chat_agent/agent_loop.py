"""Agent 主循环 — ReAct 模式（Reasoning + Acting）。

用户输入 → DeepSeek 判断意图 → 调用工具(最多5轮) → 生成回复
涉及写操作（发邮件）时返回确认卡片，等用户确认后执行。

重要：所有 API 调用保持一致地启用思考模式，捕获 reasoning_content 并
在后续消息中回传，避免 DeepSeek V4 因模式不匹配抛出 400 错误。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from tools.deepseek_client import make_client, default_model

from src.agents.chat_agent.tools import TOOL_DEFINITIONS, TOOL_EXECUTORS

logger = logging.getLogger(__name__)


def _get_reasoning(msg) -> str | None:
    """从 API 响应的 message 对象中安全提取 reasoning_content。"""
    rc = getattr(msg, "reasoning_content", None)
    if rc:
        return rc
    if hasattr(msg, "model_extra") and msg.model_extra:
        return msg.model_extra.get("reasoning_content")
    return None


def validate_tool_call(func_name: str, func_args: dict) -> dict | None:
    """校验工具调用参数。返回 None=通过，返回 dict=错误信息。"""
    # 1. 查找工具定义
    schema = None
    for td in TOOL_DEFINITIONS:
        if td["function"]["name"] == func_name:
            schema = td["function"]
            break
    if not schema:
        return {
            "error": f"工具 '{func_name}' 不存在",
            "available_tools": list(TOOL_EXECUTORS.keys()),
            "instruction": "请只用以上已注册工具，不要编造其他工具名。",
        }

    params_schema = schema.get("parameters", {})
    properties = params_schema.get("properties", {})
    required = params_schema.get("required", [])

    # 2. 检查必填参数
    for param_name in required:
        val = func_args.get(param_name)
        if val is None or val == "":
            return {
                "error": f"缺少必填参数 '{param_name}'",
                "required": required,
                "your_params": list(func_args.keys()),
            }

    # 3. 检查额外参数（模型编造的参数在这里被拦截）
    for arg_name in func_args:
        if arg_name.startswith("_"):
            continue  # 系统内部参数
        if arg_name not in properties:
            return {
                "error": f"参数 '{arg_name}' 不存在于 {func_name} 中",
                "valid_params": list(properties.keys()),
                "invalid_param": arg_name,
                "instruction": f"请只用有效参数 {list(properties.keys())}，不要添加额外参数。",
            }

    # 4. 类型校验（integer）
    for arg_name, arg_value in func_args.items():
        prop = properties.get(arg_name, {})
        if prop.get("type") == "integer" and arg_value is not None:
            try:
                int(arg_value)
            except (ValueError, TypeError):
                return {
                    "error": f"参数 '{arg_name}' 应为整数，实际值: {repr(arg_value)}",
                    "expected": "integer",
                }

    # 5. 枚举校验
    for arg_name, arg_value in func_args.items():
        prop = properties.get(arg_name, {})
        enum_vals = prop.get("enum", [])
        if enum_vals and arg_value not in enum_vals:
            return {
                "error": f"参数 '{arg_name}' 的值 '{arg_value}' 无效",
                "allowed_values": enum_vals,
            }

    return None  # 校验通过


SYSTEM_PROMPT = """你是"小贸"，外贸客户平台的技术助手。

## 仅有的 5 个函数（精确名称，禁止使用任何其他名称）

```
1. search_knowledge_base(query: str, collection?: "产品信息"|"公司文档"|"采购表单")
2. search_customers(query: str)
3. get_customer_detail(customer_id: int)
4. generate_inquiry_email(customer_id: int, language?: "auto"|"zh"|"en")
5. list_email_status(customer_id?: int)
```

函数名就是 API 端点——不存在就是真的没有，不要自己发明。
禁止使用的假函数：view_email_status、send_email、check_email、get_email、send_mail 等。

## 行为铁律

1. 只调上面 5 个函数。函数名必须一字不差，参数名也必须一字不差。
2. 工具返回 "未知工具" 错误 → 立刻停止，告诉用户你做不到，不要换名字重试。
3. generate_inquiry_email 只生成草稿。确认后用户去「询盘邮件」页面发送。你不发邮件。
4. 搜不到就说没找到，不要编造信息。
5. 不要说"已发送""发送成功"——你没这个能力。

## 防乱码

知识库部分是 OCR 识别文字，可能有乱码。无意义字符/碎片 → 直接丢弃，回复"未找到相关可读内容"。不要复制乱码给用户。英文内容翻译成中文。

## 回复格式

简洁，先结论后细节。必要时分点（不超过 5 条）。不需要问候和"还有什么可以帮您"。


def _summarize_history(history: list[dict], client, model: str) -> str:
    """把旧对话摘要成一段精简文字，保留关键事实。"""
    # 取旧的用户+助手消息（排除最近 6 条）
    old = [h for h in history[:-6] if h.get("role") in ("user", "assistant")]
    if not old:
        return ""

    # 只取前 3000 字做摘要
    text = ""
    for h in old:
        text += f"[{h['role']}]: {h.get('content', '')[:300]}\n"
        if len(text) > 3000:
            break

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": f"用 2-3 句话（中文、100 字内）总结以下对话的关键事实（产品、客户、操作等），只写摘要不要废话：\n\n{text}"
            }],
            temperature=0.1,
            max_tokens=200,
            extra_body={"thinking": {"type": "disabled"}},
        )
        return resp.choices[0].message.content.strip()[:200]
    except Exception:
        return ""


def run_agent(
    user_message: str,
    history: list[dict] | None = None,
    *,
    model: str | None = None,
    max_rounds: int = 5,
) -> dict:
    """执行 Agent 循环，返回给前端的回复。

    Returns:
        {
            "reply": "回复文本（markdown）",
            "confirm": {"type": "send_email", "customer_id": 1, "subject": "...", "body": "..."} | None,
            "tool_calls": [{"name": "...", "result": {...}}],  # 调试用
        }
    """
    client = make_client()
    mdl = model or default_model()

    system_prompt = SYSTEM_PROMPT

    # 智能上下文管理：历史太长时自动摘要压缩
    if history:
        total_chars = sum(len(h.get("content", "")) for h in history)
        if total_chars > 6000:
            summary = _summarize_history(history, client, mdl)
            if summary:
                system_prompt += f"\n\n【之前的对话要点】{summary}"
            # 只保留最近 6 条原始消息
            recent = [h for h in history[-6:] if h.get("role") in ("user", "assistant")]
        else:
            recent = [h for h in history[-20:] if h.get("role") in ("user", "assistant")]

        for h in recent:
            messages.append({"role": h["role"], "content": h.get("content", "")})
    else:
        messages = []

    messages = [{"role": "system", "content": system_prompt}] + messages
    messages.append({"role": "user", "content": user_message})

    tool_calls_log: list[dict] = []
    confirm_card: dict | None = None

    for round_num in range(max_rounds):
        try:
            resp = client.chat.completions.create(
                model=mdl,
                messages=messages,
                temperature=0.3,
                max_tokens=2048,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
            )
        except Exception as e:
            logger.exception("DeepSeek API 调用失败")
            return {"reply": f"抱歉，服务暂时不可用（{str(e)[:100]}），请稍后重试。", "tool_calls": tool_calls_log}

        choice = resp.choices[0]
        msg = choice.message

        # 捕获 reasoning_content（多轮对话必须回传）
        reasoning = _get_reasoning(msg)

        # 模型返回了 function_call
        if msg.tool_calls:
            for tc in msg.tool_calls:
                func_name = tc.function.name
                try:
                    func_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    func_args = {"_parse_error": True, "_raw": tc.function.arguments[:200]}

                logger.info("Agent 调用工具: %s(%s)", func_name, func_args)

                # 硬校验：参数名、类型、枚举、必填
                validation_error = validate_tool_call(func_name, func_args)
                if validation_error:
                    result = validation_error
                else:
                    executor = TOOL_EXECUTORS.get(func_name)
                    if executor:
                        result = executor(func_args)
                    else:
                        result = {
                            "error": f"工具 '{func_name}' 不存在",
                            "available_tools": list(TOOL_EXECUTORS.keys()),
                            "instruction": "你调用了不存在的工具。请只用以上5个已注册工具。如果无法完成任务，直接告知用户。禁止换名字重试。",
                        }

                tool_calls_log.append({"name": func_name, "args": func_args, "result": result})

                # 检查是否需要用户确认
                if result.get("needs_confirm"):
                    confirm_card = {
                        "type": "send_email",
                        "customer_id": result.get("customer_id"),
                        "company_name": result.get("company_name"),
                        "subject": result.get("subject"),
                        "body": result.get("body"),
                    }

                # 工具结果追加到消息（含 reasoning_content 以保持多轮一致性）
                assistant_msg = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": func_name,
                                "arguments": tc.function.arguments,
                            },
                        }
                    ],
                }
                if reasoning:
                    assistant_msg["reasoning_content"] = reasoning
                messages.append(assistant_msg)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

            # 如果有确认卡片，停止循环，让用户确认
            if confirm_card:
                break

            continue  # 继续下一轮（可能再调工具）

        # 模型返回纯文本
        reply = msg.content or ""
        return {"reply": reply, "confirm": confirm_card, "tool_calls": tool_calls_log}

    # 达到最大轮数，生成最终回复
    if confirm_card:
        return {
            "reply": f"已为 **{confirm_card['company_name']}** 生成邮件草稿：\n\n**主题：** {confirm_card['subject']}\n\n---\n\n{confirm_card['body'][:500]}{'...' if len(confirm_card.get('body', '')) > 500 else ''}\n\n---\n\n确认内容无误？点击「✓ 确认」后，请前往「询盘邮件」页面进行实际发送（我无法代发邮件）。",
            "confirm": confirm_card,
            "tool_calls": tool_calls_log,
        }

    try:
        final_resp = client.chat.completions.create(
            model=mdl,
            messages=messages + [{"role": "user", "content": "请用中文总结以上工具调用的结果，简洁回答用户。"}],
            temperature=0.3,
            max_tokens=1024,
        )
        reply = final_resp.choices[0].message.content or ""
    except Exception:
        reply = "已执行相关操作，请查看结果。"

    return {"reply": reply, "confirm": confirm_card, "tool_calls": tool_calls_log}


def run_agent_stream(user_message: str, history: list[dict] | None = None, *, model: str | None = None, max_rounds: int = 5):
    """流式 Agent：yield SSE 事件 (event_type, data_json)。

    事件类型: thinking | tool_start | tool_result | content | done | error
    """
    import json as _json
    client = make_client()
    mdl = model or default_model()

    system_prompt = SYSTEM_PROMPT

    # 智能上下文管理
    if history:
        total_chars = sum(len(h.get("content", "")) for h in history)
        if total_chars > 6000:
            summary = _summarize_history(history, client, mdl)
            if summary:
                system_prompt += f"\n\n【之前的对话要点】{summary}"
            recent = [h for h in history[-6:] if h.get("role") in ("user", "assistant")]
        else:
            recent = [h for h in history[-20:] if h.get("role") in ("user", "assistant")]
    else:
        recent = []

    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    for h in recent:
        messages.append({"role": h["role"], "content": h.get("content", "")})
    messages.append({"role": "user", "content": user_message})

    tool_calls_log: list[dict] = []
    confirm_card: dict | None = None

    for round_num in range(max_rounds):
        try:
            resp = client.chat.completions.create(
                model=mdl, messages=messages,
                temperature=0.3, max_tokens=2048,
                tools=TOOL_DEFINITIONS, tool_choice="auto",
            )
        except Exception as e:
            yield ("error", _json.dumps({"message": str(e)[:100]}, ensure_ascii=False))
            return

        choice = resp.choices[0]
        msg = choice.message

        # 捕获 reasoning_content（多轮对话必须回传，否则 400）
        reasoning = _get_reasoning(msg)
        if reasoning:
            yield ("thinking", _json.dumps({"text": reasoning}, ensure_ascii=False))

        if msg.tool_calls:
            for tc in msg.tool_calls:
                func_name = tc.function.name
                try:
                    func_args = _json.loads(tc.function.arguments)
                except Exception:
                    func_args = {"_parse_error": True, "_raw": tc.function.arguments[:200]}

                yield ("tool_start", _json.dumps({"name": func_name, "args": func_args}, ensure_ascii=False))

                # 硬校验：参数名、类型、枚举、必填
                validation_error = validate_tool_call(func_name, func_args)
                if validation_error:
                    result = validation_error
                else:
                    executor = TOOL_EXECUTORS.get(func_name)
                    if executor:
                        result = executor(func_args)
                    else:
                        result = {
                            "error": f"工具 '{func_name}' 不存在",
                            "available_tools": list(TOOL_EXECUTORS.keys()),
                            "instruction": "你调用了不存在的工具。请只用以上5个已注册工具。如果无法完成任务，直接告知用户。禁止换名字重试。",
                        }
                tool_calls_log.append({"name": func_name, "args": func_args, "result": result})

                yield ("tool_result", _json.dumps({"name": func_name, "result": result}, ensure_ascii=False))

                if result.get("needs_confirm"):
                    confirm_card = {
                        "type": "send_email", "customer_id": result.get("customer_id"),
                        "company_name": result.get("company_name"),
                        "subject": result.get("subject"), "body": result.get("body"),
                    }

                # 追加工具消息（含 reasoning_content 以保持多轮一致性）
                assistant_msg: dict = {
                    "role": "assistant", "content": None,
                    "tool_calls": [{"id": tc.id, "type": "function",
                        "function": {"name": func_name, "arguments": tc.function.arguments}}],
                }
                if reasoning:
                    assistant_msg["reasoning_content"] = reasoning
                messages.append(assistant_msg)
                messages.append({"role": "tool", "tool_call_id": tc.id,
                    "content": _json.dumps(result, ensure_ascii=False)})

            if confirm_card:
                reply = f"已为 **{confirm_card['company_name']}** 生成邮件草稿：\n\n**主题：** {confirm_card['subject']}\n\n---\n\n{confirm_card['body'][:500]}{'...' if len(confirm_card.get('body', '')) > 500 else ''}\n\n---\n\n确认内容无误？点击「✓ 确认」后，请前往「询盘邮件」页面进行实际发送（我无法代发邮件）。"
                yield ("content", _json.dumps({"text": reply}, ensure_ascii=False))
                yield ("done", _json.dumps({"confirm": confirm_card, "tool_calls": tool_calls_log}, ensure_ascii=False))
                return
            continue

        # 纯文本回复 → 流式输出
        break

    # 流式输出最终回复
    try:
        stream = client.chat.completions.create(
            model=mdl, messages=messages,
            temperature=0.3, max_tokens=2048,
            stream=True,
        )
        full_content = ""
        thinking_content = ""
        in_thinking = True

        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue

            # 思维链内容
            if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                thinking_content += delta.reasoning_content
                yield ("thinking", _json.dumps({"text": delta.reasoning_content}, ensure_ascii=False))

            # 正文内容
            if delta.content:
                if in_thinking and thinking_content:
                    in_thinking = False  # 思维链结束
                full_content += delta.content
                yield ("content", _json.dumps({"text": delta.content}, ensure_ascii=False))

        reply = full_content.strip() or "抱歉，未能生成回复。"
    except Exception as e:
        reply = f"抱歉，流式输出失败: {str(e)[:100]}"
        yield ("content", _json.dumps({"text": reply}, ensure_ascii=False))

    yield ("done", _json.dumps({"confirm": confirm_card, "tool_calls": tool_calls_log}, ensure_ascii=False))


def execute_confirmed_action(confirm: dict) -> dict:
    """执行用户确认的操作。"""
    action_type = confirm.get("type", "")

    if action_type == "send_email":
        # 用户确认发送邮件 — 更新状态为 confirmed
        cid = confirm.get("customer_id")
        if cid:
            from src.core.database import get_db
            db = get_db()
            db.execute(
                "UPDATE customer SET email_status='confirmed', updated_at=datetime('now','localtime') "
                "WHERE id=? AND email_status='draft'",
                (int(cid),),
            )
            db.commit()
            return {
                "status": "confirmed",
                "customer_id": cid,
                "message": f"邮件已确认，可在询盘邮件页面批量发送。",
            }
        return {"status": "error", "message": "客户ID无效"}

    return {"status": "error", "message": f"未知操作类型: {action_type}"}
