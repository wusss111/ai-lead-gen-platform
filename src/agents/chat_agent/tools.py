# -*- coding: utf-8 -*-
"""Agent 工具定义 + 执行函数."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# DeepSeek Function Calling 工具定义(OpenAI 兼容格式)
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge_base",
            "description": "search_knowledge_base:搜索知识库.参数query(搜索词,必填)和collection(可选,限定产品信息/公司文档/采购表单).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询词,建议使用关键词而非完整句子",
                    },
                    "collection": {
                        "type": "string",
                        "enum": ["产品信息", "公司文档", "采购表单"],
                        "description": "限定搜索的知识库分类,不传则搜索全部",
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
            "description": "search_customers:搜索CRM客户库.参数query(公司名或邮箱关键词,必填).返回匹配客户的基本信息和评分.",
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
            "description": "get_customer_detail:获取客户完整信息.参数customer_id(整数,必填).返回评估结果、评分、跟进建议等全部字段.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "integer",
                        "description": "客户ID(整数,必填)",
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
            "description": "generate_inquiry_email:生成询盘邮件草稿(不是发送!).参数customer_id(整数,必填)和language(可选,auto/zh/en).生成后需用户确认.",
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
                        "description": "邮件语言:auto(自动检测)/zh(中文)/en(英文),默认auto",
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
            "description": "list_email_status:查询邮件发送状态和阅读状态.参数customer_id(整数,可选,不传则列出所有).这是唯一的邮件状态查询函数,不要使用view_email_status、check_email等其他名称.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "integer",
                        "description": "客户ID(可选,不传则列出所有有邮件记录的客户)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "smart_search_customers",
            "description": "smart_search_customers:AI 自然语言搜索客户.参数q(自然语言描述,必填).例如'德国的高分客户'、'最近一周导入的客户'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "自然语言查询,描述你想找什么样的客户"},
                },
                "required": ["q"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_salespersons",
            "description": "list_salespersons:列出所有销售负责人及其名下客户数量.无参数.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assign_customer",
            "description": "assign_customer:将客户分配给销售负责人.参数customer_id(客户ID,必填)和salesperson_id(销售ID,必填,传0表示取消分配).需用户确认.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "integer", "description": "客户ID"},
                    "salesperson_id": {"type": "integer", "description": "销售负责人ID,传0表示取消分配"},
                },
                "required": ["customer_id", "salesperson_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "export_customers",
            "description": "export_customers:导出客户数据.参数deal_recommendation(可选)和batch_id(可选)用于筛选.返回客户列表JSON.",
            "parameters": {
                "type": "object",
                "properties": {
                    "deal_recommendation": {"type": "string", "description": "筛选推荐等级:high_intent/watch/no"},
                    "batch_id": {"type": "string", "description": "筛选批次ID"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_emailable_customers",
            "description": "list_emailable_customers:列出可发送邮件的客户(有邮箱且未发送过的).参数search(可选,搜索公司名)、email_status(可选,按邮件状态筛选).",
            "parameters": {
                "type": "object",
                "properties": {
                    "search": {"type": "string", "description": "搜索关键词"},
                    "email_status": {"type": "string", "description": "邮件状态筛选:draft/confirmed/sent/failed"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_email_draft",
            "description": "update_email_draft:编辑客户的邮件草稿(主题或正文).只能修改未发送的邮件.参数customer_id(必填)、subject(可选)、body(可选).需用户确认.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "integer", "description": "客户ID"},
                    "subject": {"type": "string", "description": "新邮件主题"},
                    "body": {"type": "string", "description": "新邮件正文"},
                },
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_job_status",
            "description": "check_job_status:查询客户评估任务的进度和状态.参数job_id(任务ID,必填).",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "评估任务ID(UUID格式)"},
                },
                "required": ["job_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_kb_collections",
            "description": "list_kb_collections:查看知识库概况,包括三个集合(产品信息/公司文档/采购表单)各自的文档数和数据块数.无参数.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

# -- 工具执行函数 --


def execute_search_knowledge_base(args: dict) -> dict:
    """执行知识库搜索."""
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
    """执行客户搜索."""
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
    """获取客户详情."""
    from src.core.database import get_db

    cid = args.get("customer_id")
    if not cid:
        return {"found": False, "message": "请提供客户ID"}
    try:
        cid = int(cid)
    except (ValueError, TypeError):
        return {"found": False, "message": f"customer_id 必须是整数,实际值: {repr(cid)}"}

    db = get_db()
    row = db.execute(
        "SELECT c.*, COALESCE(s.name, '') as salesperson_name "
        "FROM customer c LEFT JOIN salesperson s ON c.assigned_salesperson_id = s.id "
        "WHERE c.id=?", (cid,)
    ).fetchone()

    if not row:
        return {"found": False, "message": f"客户ID {cid} 不存在"}

    from src.core.database import dicts_from_rows
    return {"found": True, "customer": dicts_from_rows([row])[0]}


def execute_generate_inquiry_email(args: dict) -> dict:
    """生成询盘邮件草稿.返回需要用户确认."""
    from src.core.database import get_db

    cid = args.get("customer_id")
    language = args.get("language", "auto")

    if not cid:
        return {"status": "error", "message": "请提供客户ID"}
    try:
        cid = int(cid)
    except (ValueError, TypeError):
        return {"status": "error", "message": f"customer_id 必须是整数,实际值: {repr(cid)}"}

    db = get_db()
    row = db.execute(
        "SELECT c.*, COALESCE(s.name, '') as salesperson_name "
        "FROM customer c LEFT JOIN salesperson s ON c.assigned_salesperson_id = s.id "
        "WHERE c.id=? AND c.contact_email IS NOT NULL AND c.contact_email != ''",
        (cid,),
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
        logger.warning("知识库检索失败,跳过: %s", e)

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
        "message": f"邮件草稿已生成.请确认内容后发送.",
    }


def execute_list_email_status(args: dict) -> dict:
    """查看邮件状态."""
    from src.core.database import get_db, dicts_from_rows

    db = get_db()
    cid = args.get("customer_id")

    if cid:
        try:
            cid = int(cid)
        except (ValueError, TypeError):
            return {"found": False, "message": f"customer_id 必须是整数,实际值: {repr(cid)}"}
        row = db.execute(
            "SELECT id, company_name, contact_email, email_status, email_sent_at, "
            "tracking_last_opened_at FROM customer WHERE id=?",
            (cid,),
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


# -- 新增工具执行函数 (8个) --

def execute_smart_search_customers(args: dict) -> dict:
    """AI 自然语言搜索客户."""
    from src.core.database import get_db, dicts_from_rows
    from tools.deepseek_client import chat_json

    q = args.get("q", "").strip()
    if not q:
        return {"found": False, "message": "请提供搜索描述"}

    db = get_db()
    # 构建 schema 提示,让 DeepSeek 生成 SQL
    schema_hint = """customer 表列:
id, company_name, website, country_region, contact_name, contact_email,
target_products, priority, overall_score_computed, deal_recommendation,
buyer_seller_role, manual_review_flag, data_quality, email_status, created_at"""

    messages = [
        {"role": "system", "content": f"你是SQL专家.根据用户的自然语言生成一条SELECT语句.只输出JSON: {{\"sql\":\"...\", \"explanation\":\"...\"}}.表结构:{schema_hint}"},
        {"role": "user", "content": q},
    ]
    resp = chat_json(messages, model=None, temperature=0.1, max_tokens=512)
    sql = (resp.get("sql") or "").strip()
    if not sql or not sql.upper().startswith("SELECT"):
        return {"found": False, "message": "无法生成有效查询"}

    # 安全检查
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "EXEC", "ATTACH"]
    if any(w in sql.upper() for w in forbidden):
        return {"found": False, "message": "生成的SQL包含禁止操作"}

    try:
        rows = db.execute(sql).fetchall()
    except Exception as e:
        return {"found": False, "message": f"查询执行失败: {e}"}

    return {"found": True, "count": len(rows), "explanation": resp.get("explanation", ""),
            "sql": sql, "customers": dicts_from_rows(rows)}


def execute_list_salespersons(args: dict) -> dict:
    """列出销售负责人."""
    from src.core.database import get_db, dicts_from_rows
    db = get_db()
    rows = db.execute(
        "SELECT s.*, COUNT(c.id) as customer_count FROM salesperson s "
        "LEFT JOIN customer c ON c.assigned_salesperson_id = s.id "
        "GROUP BY s.id ORDER BY s.active DESC, s.name"
    ).fetchall()
    return {"found": True, "count": len(rows), "salespersons": dicts_from_rows(rows)}


def execute_assign_customer(args: dict) -> dict:
    """分配客户给销售."""
    from src.core.database import get_db
    db = get_db()
    cid = args.get("customer_id")
    sid = args.get("salesperson_id")
    if not cid:
        return {"status": "error", "message": "请提供客户ID"}
    if sid is None:
        return {"status": "error", "message": "请提供销售负责人ID(传0取消分配)"}

    # 验证客户存在
    row = db.execute("SELECT id, company_name FROM customer WHERE id=?", (int(cid),)).fetchone()
    if not row:
        return {"status": "error", "message": f"客户ID {cid} 不存在"}

    if int(sid) == 0:
        db.execute("UPDATE customer SET assigned_salesperson_id=NULL, updated_at=datetime('now','localtime') WHERE id=?", (int(cid),))
        db.commit()
        return {"status": "ok", "customer_id": int(cid), "company_name": row["company_name"],
                "assigned_to": None, "message": "已取消分配"}
    else:
        sp = db.execute("SELECT id, name FROM salesperson WHERE id=?", (int(sid),)).fetchone()
        if not sp:
            return {"status": "error", "message": f"销售负责人ID {sid} 不存在"}
        db.execute("UPDATE customer SET assigned_salesperson_id=?, updated_at=datetime('now','localtime') WHERE id=?", (int(sid), int(cid)))
        db.commit()
        return {"status": "ok", "customer_id": int(cid), "company_name": row["company_name"],
                "assigned_to": sp["name"], "needs_confirm": False}


def execute_export_customers(args: dict) -> dict:
    """导出客户数据."""
    from src.core.database import get_db, dicts_from_rows
    db = get_db()
    conditions = []
    params = []
    if args.get("deal_recommendation"):
        conditions.append("deal_recommendation=?")
        params.append(args["deal_recommendation"])
    if args.get("batch_id"):
        conditions.append("batch_id=?")
        params.append(args["batch_id"])
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = db.execute(f"SELECT * FROM customer {where} ORDER BY overall_score_computed DESC LIMIT 500", params).fetchall()
    return {"found": True, "count": len(rows), "customers": dicts_from_rows(rows)}


def execute_list_emailable_customers(args: dict) -> dict:
    """列出可发邮件的客户."""
    from src.core.database import get_db, dicts_from_rows
    db = get_db()
    conditions = ["contact_email IS NOT NULL", "contact_email != ''"]
    params = []
    if args.get("search"):
        conditions.append("company_name LIKE ?")
        params.append(f"%{args['search']}%")
    if args.get("email_status"):
        conditions.append("email_status=?")
        params.append(args["email_status"])
    where = "WHERE " + " AND ".join(conditions)
    rows = db.execute(
        f"SELECT id, company_name, contact_name, contact_email, country_region, email_status, deal_recommendation "
        f"FROM customer {where} ORDER BY overall_score_computed DESC LIMIT 50", params
    ).fetchall()
    return {"found": True, "count": len(rows), "customers": dicts_from_rows(rows)}


def execute_update_email_draft(args: dict) -> dict:
    """编辑邮件草稿."""
    from src.core.database import get_db
    db = get_db()
    cid = args.get("customer_id")
    if not cid:
        return {"status": "error", "message": "请提供客户ID"}

    row = db.execute(
        "SELECT id, company_name, email_status FROM customer WHERE id=?", (int(cid),)
    ).fetchone()
    if not row:
        return {"status": "error", "message": f"客户ID {cid} 不存在"}
    if row["email_status"] in ("sent", None):
        return {"status": "error", "message": f"只能编辑草稿状态的邮件,当前状态: {row['email_status']}"}

    updates = []
    params = []
    if args.get("subject"):
        updates.append("email_subject=?")
        params.append(args["subject"])
    if args.get("body"):
        updates.append("email_body=?")
        params.append(args["body"])
    if not updates:
        return {"status": "error", "message": "请提供要修改的内容(subject 或 body)"}

    params.append(int(cid))
    db.execute(
        f"UPDATE customer SET {', '.join(updates)}, updated_at=datetime('now','localtime') WHERE id=?",
        params
    )
    db.commit()
    return {"status": "updated", "customer_id": int(cid), "company_name": row["company_name"],
            "message": "邮件草稿已更新"}


def execute_check_job_status(args: dict) -> dict:
    """查询评估任务进度."""
    from src.core.database import get_db, dicts_from_rows
    import os, json

    job_id = args.get("job_id", "").strip()
    if not job_id:
        return {"found": False, "message": "请提供任务ID"}

    db = get_db()
    batch = db.execute("SELECT * FROM evaluation_batch WHERE id=?", (job_id,)).fetchone()
    if not batch:
        return {"found": False, "message": f"任务 {job_id} 不存在"}

    result = dict(batch)
    # 尝试读取 RQ job 进度
    job_dir = None
    from src.core.config import get_config
    cfg = get_config()
    for d in os.listdir(str(cfg.data_dir / "jobs")):
        if d.startswith(job_id[:8]):
            job_dir = cfg.data_dir / "jobs" / d
            break
    if job_dir:
        progress_file = job_dir / "progress.json"
        if progress_file.exists():
            result["progress"] = json.loads(progress_file.read_text())
        rq_file = job_dir / "rq_job_id.txt"
        if rq_file.exists():
            result["rq_job_id"] = rq_file.read_text().strip()
    return {"found": True, "job": result}


def execute_list_kb_collections(args: dict) -> dict:
    """查看知识库概况."""
    from tools.vector_store import get_collections, get_collection_stats
    cols = get_collections()
    result = []
    for c in cols:
        stats = get_collection_stats(c["name"])
        result.append({"name": c["name"], **stats})
    return {"found": True, "collections": result}


# 工具名 → 执行函数映射
TOOL_EXECUTORS = {
    "search_knowledge_base": execute_search_knowledge_base,
    "search_customers": execute_search_customers,
    "get_customer_detail": execute_get_customer_detail,
    "generate_inquiry_email": execute_generate_inquiry_email,
    "list_email_status": execute_list_email_status,
    "smart_search_customers": execute_smart_search_customers,
    "list_salespersons": execute_list_salespersons,
    "assign_customer": execute_assign_customer,
    "export_customers": execute_export_customers,
    "list_emailable_customers": execute_list_emailable_customers,
    "update_email_draft": execute_update_email_draft,
    "check_job_status": execute_check_job_status,
    "list_kb_collections": execute_list_kb_collections,
}
