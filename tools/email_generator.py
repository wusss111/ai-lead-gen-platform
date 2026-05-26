"""Generate customized inquiry emails using DeepSeek LLM."""

from __future__ import annotations

import logging
from typing import Any, Callable

from tools.deepseek_client import chat_json

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是外贸 B2B 询盘邮件撰写助手。根据目标客户的评估结果，撰写专业、个性化的询盘开发信。

规则：
1. 输出必须是合法 JSON 对象，不要 markdown 代码围栏。
2. 邮件必须包含 subject（主题）、body_text（纯文本正文）、body_html（HTML 正文，可选）。
3. 语气：专业、简洁、有针对性；根据买方/卖方角色调整口吻。
4. 语言：根据客户所在国家/地区自动选择最合适的商务沟通语言。法国→法语，德国→德语，西班牙→西班牙语，日本→日语，中国/香港/台湾→简体中文。国家/地区为空或无法判断时，默认使用英语。
5. 必须引用评估结果中的具体依据（产品匹配点、能力信号），避免泛泛而谈。
6. 署名使用配置的发件人名称和公司。
7. deal_recommendation 为 "no" 时 skip=true，不生成邮件。
8. deal_recommendation 为 "watch" 时语气偏试探性，简短为宜。
9. deal_recommendation 为 "high_intent" 时可详细介绍产品与合作可能。
10. 对方没有 contact_name 时根据目标语言使用对应的称呼（Dear/Monsieur/Sehr geehrte 等）。

JSON 必填字段：
- subject: 字符串，邮件主题
- body_text: 字符串，纯文本正文
- body_html: 字符串，HTML 正文（可为空字符串）
- skip: 布尔值，是否跳过该邮件
- skip_reason: 字符串，跳过原因（skip 为 true 时必填）"""


def build_email_messages(
    *,
    company_name: str = "",
    contact_name: str = "",
    country_region: str = "",
    target_products: str = "",
    product_fit_reasons: str = "",
    capability_signals: str = "",
    deal_recommendation: str = "",
    buyer_seller_role: str = "",
    buyer_seller_reason: str = "",
    next_action: str = "",
    website: str = "",
    notes: str = "",
    from_name: str = "销售团队",
    from_company: str = "",
    language: str = "zh",
    knowledge_context: str = "",
) -> list[dict[str, str]]:
    """Build messages for DeepSeek email generation."""

    user_lines = [
        f"公司名称：{company_name}",
        f"联系人：{contact_name or '未知'}",
        f"国家/地区：{country_region}",
        f"网站：{website}",
        f"目标产品：{target_products}",
        f"产品匹配依据：{product_fit_reasons}",
        f"能力信号：{capability_signals}",
        f"买方/卖方角色：{buyer_seller_role}（{buyer_seller_reason}）",
        f"合作建议等级：{deal_recommendation}",
        f"建议跟进动作：{next_action}",
        f"语言：{'请根据客户所在国家/地区自动选择最合适的商务沟通语言（' + country_region + '）' if language == 'auto' else '英文' if language == 'en' else '简体中文'}",
        f"发件人：{from_name}" + (f"（{from_company}）" if from_company else ""),
    ]
    if notes:
        user_lines.append(f"备注：{notes}")
    if knowledge_context.strip():
        user_lines.append(f"\n【相关知识库内容，请在邮件中自然地融入以下产品卖点和公司优势】\n{knowledge_context.strip()}")

    user_prompt = "\n".join(user_lines)

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def generate_single_email(
    *,
    company_name: str = "",
    contact_name: str = "",
    country_region: str = "",
    target_products: str = "",
    product_fit_reasons: str = "",
    capability_signals: str = "",
    deal_recommendation: str = "",
    buyer_seller_role: str = "",
    buyer_seller_reason: str = "",
    next_action: str = "",
    website: str = "",
    notes: str = "",
    from_name: str = "销售团队",
    from_company: str = "",
    language: str = "zh",
    knowledge_context: str = "",
    model: str | None = None,
) -> dict[str, Any]:
    """Generate a single inquiry email via DeepSeek."""

    # Quick skip: no recommendation or explicit "no"
    if not deal_recommendation or deal_recommendation == "no":
        return {
            "subject": "", "body_text": "", "body_html": "",
            "skip": True,
            "skip_reason": "评估建议为不跟进" if deal_recommendation == "no" else "缺少评估建议",
        }

    messages = build_email_messages(
        company_name=company_name,
        contact_name=contact_name,
        country_region=country_region,
        target_products=target_products,
        product_fit_reasons=product_fit_reasons,
        capability_signals=capability_signals,
        deal_recommendation=deal_recommendation,
        buyer_seller_role=buyer_seller_role,
        buyer_seller_reason=buyer_seller_reason,
        next_action=next_action,
        website=website,
        notes=notes,
        from_name=from_name,
        from_company=from_company,
        language=language,
        knowledge_context=knowledge_context,
    )

    try:
        result = chat_json(messages, model=model, temperature=0.7, max_tokens=2048)
        result.setdefault("body_html", "")
        result.setdefault("skip", False)
        result.setdefault("skip_reason", "")
        return result
    except Exception as e:
        logger.warning("Email generation failed for %s: %s", company_name, e)
        return {
            "subject": "", "body_text": "", "body_html": "",
            "skip": True,
            "skip_reason": f"生成失败: {e}",
        }


def generate_emails_batch(
    rows: list[dict[str, Any]],
    *,
    from_name: str = "销售团队",
    from_company: str = "",
    language: str = "zh",
    model: str | None = None,
    progress_callback: Callable | None = None,
) -> list[dict[str, Any]]:
    """Generate emails for multiple customers."""
    results = []
    total = len(rows)
    for i, row in enumerate(rows):
        if progress_callback:
            progress_callback({
                "phase": "generate",
                "current": i + 1,
                "total": total,
                "message": f"生成邮件 {i + 1}/{total}: {row.get('company_name', '')}",
            })

        email = generate_single_email(
            company_name=str(row.get("company_name", "")),
            contact_name=str(row.get("contact_name", "")),
            country_region=str(row.get("country_region", "")),
            target_products=str(row.get("target_products", "")),
            product_fit_reasons=str(row.get("product_fit_reasons", "")),
            capability_signals=str(row.get("capability_signals", "")),
            deal_recommendation=str(row.get("deal_recommendation", "")),
            buyer_seller_role=str(row.get("buyer_seller_role", "")),
            buyer_seller_reason=str(row.get("buyer_seller_reason", "")),
            next_action=str(row.get("next_action", "")),
            website=str(row.get("website", "")),
            notes=str(row.get("notes", "")),
            from_name=from_name,
            from_company=from_company,
            language=language,
            knowledge_context=str(row.get("knowledge_context", "")),
            model=model,
        )

        results.append({
            "customer_id": row.get("id"),
            "company_name": row.get("company_name"),
            "contact_name": row.get("contact_name"),
            "contact_email": row.get("contact_email"),
            "deal_recommendation": row.get("deal_recommendation"),
            **email,
        })

    return results


# ---------------------------------------------------------------------------
# Reply generation — AI drafts a reply to a customer's incoming email
# ---------------------------------------------------------------------------

REPLY_SYSTEM_PROMPT = """你是外贸 B2B 邮件回复撰写助手。根据客户的原邮件内容和该客户的历史评估结果，撰写专业、得体的回复。

规则：
1. 输出必须是合法 JSON 对象，不要 markdown 代码围栏。
2. 必须包含 subject（以 "Re: " 开头）、body_text（纯文本正文）。
3. 仔细分析客户的原邮件，理解 ta 的意图（询价？索样？技术问题？合作意向？）。
4. 回复要针对性回答客户问题，不要泛泛而谈。
5. 语气：专业、热情、简洁。
6. 语言：与客户的原始邮件语言保持一致。
7. 如果客户问了暂时回答不了的问题（如具体价格），诚实表示需要确认后回复，不要编造。

JSON 必填字段：
- subject: 字符串，回信主题
- body_text: 字符串，纯文本正文
- tone: 字符串，回复语气（professional/friendly/urgent）
- needs_human_input: 布尔值，是否有些问题 AI 无法确定回答需要人工补充
- human_input_hint: 字符串，需要人工补充的具体问题（needs_human_input 为 true 时必填）"""


def generate_reply(
    *,
    original_subject: str = "",
    original_body: str = "",
    original_from: str = "",
    customer_context: str = "",
    from_name: str = "外贸团队",
    model: str | None = None,
) -> dict[str, Any]:
    """Generate a reply draft based on customer's incoming email."""
    user_content = f"""请为以下客户回信撰写回复：

【客户原始邮件】
发件人: {original_from}
主题: {original_subject}
正文:
{original_body[:2000]}

【客户背景】
{customer_context or "无额外信息"}

【发件人署名】
{from_name}"""

    messages = [
        {"role": "system", "content": REPLY_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        result = chat_json(messages, model=model)
        result.setdefault("tone", "professional")
        result.setdefault("needs_human_input", False)
        result.setdefault("human_input_hint", "")
        return result
    except Exception as e:
        logger.warning("Reply generation failed: %s", e)
        return {
            "subject": "",
            "body_text": "",
            "tone": "professional",
            "needs_human_input": True,
            "human_input_hint": f"生成失败: {e}",
        }


def _build_customer_context(customer_id: int) -> str:
    """Build a concise context string from customer DB record for reply generation."""
    from src.core.database import get_db, dict_from_row
    db = get_db()
    row = db.execute(
        "SELECT company_name, contact_name, country_region, target_products, "
        "deal_recommendation, product_fit_reasons, capability_signals, next_action, "
        "email_subject, email_body FROM customer WHERE id=?",
        (customer_id,),
    ).fetchone()
    if not row:
        return ""
    data = dict(row)
    parts = []
    for key in ("company_name", "contact_name", "country_region", "target_products",
                "deal_recommendation", "product_fit_reasons", "capability_signals", "next_action"):
        val = data.get(key, "")
        if val:
            parts.append(f"{key}: {val}")
    if data.get("email_subject"):
        parts.append(f"我们上一封邮件主题: {data['email_subject']}")
    return "\n".join(parts)
