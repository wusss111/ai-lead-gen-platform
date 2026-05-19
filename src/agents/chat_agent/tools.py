"""Agent 工具定义 + 执行函数。"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# DeepSeek Function Calling 工具定义（OpenAI 兼容格式）
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "搜索企业知识库，获取产品信息、公司文档、采购表单、行业知识等。可用于回答关于公司产品、业务、资质等问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询词，建议使用关键词而非完整句子",
                    },
                    "collection": {
                        "type": "string",
                        "enum": ["产品信息", "公司文档", "采购表单"],
                        "description": "限定搜索的知识库分类，不传则搜索全部",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_customers",
            "description": "在客户资源库中搜索客户，返回匹配的公司名称、联系人、邮箱、评分、推荐等级等信息。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "公司名称或邮箱关键词",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer_detail",
            "description": "获取指定客户的完整详细信息，包括评估结果、评分明细、跟进建议等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "integer",
                        "description": "客户ID（数字）",
                    },
                },
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "generate_inquiry_email",
            "description": "为客户生成询盘邮件草稿。会结合知识库中的产品信息生成个性化邮件。邮件生成后需要用户确认。",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "integer",
                        "description": "客户ID",
                    },
                    "language": {
                        "type": "string",
                        "enum": ["auto", "zh", "en"],
                        "description": "邮件语言：auto(自动检测)/zh(中文)/en(英文)，默认auto",
                    },
                },
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_email_status",
            "description": "查看客户的邮件发送状态（草稿/已确认/已发送/失败/已读/未读）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "integer",
                        "description": "客户ID（可选，不传则列出所有有邮件记录的客户）",
                    },
                },
                "required": [],
            },
        },
    },
]

# -- 工具执行函数 --


def execute_search_knowledge_base(args: dict) -> dict:
    """执行知识库搜索。"""
    from tools.vector_store import search, search_multi

    query = args.get("query", "")
    collection = args.get("collection", "")
    collections = ["产品信息", "公司文档", "采购表单"]

    if collection and collection in collections:
        results = search(collection, query, top_k=3, mode="hybrid_rerank")
    else:
        results = search_multi(collections, query, top_k=3, mode="hybrid_rerank")

    if not results:
        return {"found": False, "message": "未找到相关知识库内容"}

    items = []
    for r in results:
        items.append({
            "content": r["chunk"][:500],
            "source": r.get("source_doc", ""),
            "relevance": r.get("rerank_score", r.get("score", 0)),
        })

    return {"found": True, "count": len(items), "results": items}


def execute_search_customers(args: dict) -> dict:
    """执行客户搜索。"""
    from src.core.database import get_db

    query = args.get("query", "").strip()
    if not query:
        return {"found": False, "message": "请提供搜索关键词"}

    db = get_db()
    kw = f"%{query}%"
    rows = db.execute(
        "SELECT id, company_name, contact_name, contact_email, country_region, "
        "overall_score_computed, deal_recommendation, email_status "
        "FROM customer WHERE company_name LIKE ? OR contact_email LIKE ? "
        "ORDER BY overall_score_computed DESC LIMIT 5",
        (kw, kw),
    ).fetchall()

    if not rows:
        return {"found": False, "message": f"未找到匹配 '{query}' 的客户"}

    from src.core.database import dicts_from_rows
    return {"found": True, "count": len(rows), "customers": dicts_from_rows(rows)}


def execute_get_customer_detail(args: dict) -> dict:
    """获取客户详情。"""
    from src.core.database import get_db

    cid = args.get("customer_id")
    if not cid:
        return {"found": False, "message": "请提供客户ID"}

    db = get_db()
    row = db.execute(
        "SELECT c.*, COALESCE(s.name, '') as salesperson_name "
        "FROM customer c LEFT JOIN salesperson s ON c.assigned_salesperson_id = s.id "
        "WHERE c.id=?", (int(cid),)
    ).fetchone()

    if not row:
        return {"found": False, "message": f"客户ID {cid} 不存在"}

    from src.core.database import dicts_from_rows
    return {"found": True, "customer": dicts_from_rows([row])[0]}


def execute_generate_inquiry_email(args: dict) -> dict:
    """生成询盘邮件草稿。返回需要用户确认。"""
    from src.core.database import get_db

    cid = args.get("customer_id")
    language = args.get("language", "auto")

    if not cid:
        return {"status": "error", "message": "请提供客户ID"}

    db = get_db()
    row = db.execute(
        "SELECT c.*, COALESCE(s.name, '') as salesperson_name "
        "FROM customer c LEFT JOIN salesperson s ON c.assigned_salesperson_id = s.id "
        "WHERE c.id=? AND c.contact_email IS NOT NULL AND c.contact_email != ''",
        (int(cid),),
    ).fetchone()

    if not row:
        return {"status": "error", "message": f"客户ID {cid} 不存在或缺少邮箱"}

    # 检索知识库
    kb_context = ""
    try:
        from tools.vector_store import search_multi
        product_query = f"{row['target_products'] or ''} {row['company_name'] or ''}"
        kb_results = search_multi(["产品信息", "公司文档"], product_query, top_k=3, mode="hybrid_rerank")
        kb_context = "\n".join(r["chunk"][:400] for r in kb_results) if kb_results else ""
    except Exception as e:
        logger.warning("知识库检索失败，跳过: %s", e)

    # 生成邮件
    from tools.email_generator import generate_single_email
    email = generate_single_email(
        company_name=row["company_name"] or "",
        contact_name=row["contact_name"] or "",
        country_region=row["country_region"] or "",
        target_products=row["target_products"] or "",
        product_fit_reasons=row["product_fit_reasons"] or "",
        capability_signals=row["capability_signals"] or "",
        deal_recommendation=row["deal_recommendation"] or "",
        buyer_seller_role=row["buyer_seller_role"] or "",
        buyer_seller_reason=row["buyer_seller_reason"] or "",
        next_action=row["next_action"] or "",
        website=row["website"] or "",
        notes=row["notes"] or "",
        language=language,
    )

    if email.get("skip"):
        return {"status": "skipped", "reason": email.get("skip_reason", "")}

    # 保存到数据库
    db.execute(
        "UPDATE customer SET email_subject=?, email_body=?, email_status='draft', "
        "updated_at=datetime('now','localtime') WHERE id=?",
        (email.get("subject", ""), email.get("body_text", ""), int(cid)),
    )
    db.commit()

    return {
        "status": "draft",
        "customer_id": int(cid),
        "company_name": row["company_name"],
        "subject": email.get("subject", ""),
        "body": email.get("body_text", ""),
        "needs_confirm": True,
        "message": f"邮件草稿已生成。请确认内容后发送。",
    }


def execute_list_email_status(args: dict) -> dict:
    """查看邮件状态。"""
    from src.core.database import get_db, dicts_from_rows

    db = get_db()
    cid = args.get("customer_id")

    if cid:
        row = db.execute(
            "SELECT id, company_name, contact_email, email_status, email_sent_at, "
            "tracking_last_opened_at FROM customer WHERE id=?",
            (int(cid),),
        ).fetchone()
        if not row:
            return {"found": False, "message": f"客户ID {cid} 不存在"}
        return {"found": True, "emails": dicts_from_rows([row])}
    else:
        rows = db.execute(
            "SELECT id, company_name, contact_email, email_status, email_sent_at, "
            "tracking_last_opened_at FROM customer "
            "WHERE email_status IN ('draft','confirmed','generated','sent','failed') "
            "ORDER BY email_status, id DESC LIMIT 20"
        ).fetchall()
        return {"found": True, "count": len(rows), "emails": dicts_from_rows(rows)}


# 工具名 → 执行函数映射
TOOL_EXECUTORS = {
    "search_knowledge_base": execute_search_knowledge_base,
    "search_customers": execute_search_customers,
    "get_customer_detail": execute_get_customer_detail,
    "generate_inquiry_email": execute_generate_inquiry_email,
    "list_email_status": execute_list_email_status,
}
