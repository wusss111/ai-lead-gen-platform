from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from tools.deepseek_client import chat_json, default_model
from tools.eval_company_fit import compact_catalog_for_prompt
from tools.pipeline.paths import DEFAULT_CATALOG_PATH, DEFAULT_KB_PATH, SCHEMA_EVAL_RESULT


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def kb_prompt_block(kb: dict[str, Any], max_chars: int = 4000) -> str:
    lines = [
        f"kb_version={kb.get('kb_version', '')}",
        f"one_liner: {kb.get('one_liner_zh', '')}",
        "target_segments: " + " | ".join(kb.get("target_segments_zh") or []),
        "differentiators: " + " | ".join(kb.get("differentiators_zh") or []),
        "negative_list: " + " | ".join(kb.get("negative_list_zh") or []),
        "compliance: " + " | ".join(kb.get("compliance_notes_zh") or []),
    ]
    text = "\n".join(lines)
    return text[:max_chars]


def _eval_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_EVAL_RESULT.read_text(encoding="utf-8"))


def build_messages(
    *,
    catalog_block: str,
    kb_block: str,
    company_name: str,
    website: str,
    country_region: str,
    target_products: str,
    notes: str,
    merged_evidence: str,
    catalog_version_note_hint: str,
) -> list[dict[str, str]]:
    system = """你是外贸 B2B 初筛助手。只根据「证据文本」「我方产品目录摘要」「我方知识包摘要」推理。
规则:
1) 输出必须是合法 JSON 对象（不要 markdown 代码围栏）。
2) 每条关键判断尽量在 citations 中给出 claim、source_url、source_snippet；来自人工粘贴的证据 source_url 可为空字符串并在 snippet 标注来源。
3) 不要编造对方未在证据中出现的财务数据、员工数、认证编号等。
4) 字段名与类型必须严格符合约定（全部必填字段缺一不可）。
5) 不要输出 overall_score_computed；综合对外分数由程序另行计算。
6) deal_recommendation 只能是: high_intent | watch | no 之一（程序会将该枚举转为中文展示）。
7) data_quality 表示证据是否足以支撑结论（high/medium/low）。
8) 自然语言内容一律使用简体中文：product_fit_reasons、capability_signals、reputation_risk 内的 facts 与 concerns、buyer_seller_reason、next_action、citations 中的 claim 与 source_snippet 等均须为简洁、专业的中文表述。
9) buyer_seller_role 表示相对我方（供货/出口方）而言，对方在证据中的主角色：buyer=对方更像采购/分销/进口需求方；seller=对方更像生产/品牌/与我方竞合的供应方；both=证据显示兼营或难以区分主角色；unclear=证据不足无法判断。不得凭猜测编造对方未出现的业务关系。

JSON 必填字段:
- product_fit_score: 整数 1-5
- product_fit_reasons: 字符串数组，至少 1 条
- capability_score: 整数 1-5
- capability_signals: 字符串数组
- reputation_risk: { facts: string[], concerns: string[], sources: string[] }
- reputation_safety_score: 整数 1-5（5 最安全）
- buyer_seller_role: buyer | seller | both | unclear（相对我方视角）
- buyer_seller_reason: 字符串，至少 1 字，简体中文简述判断依据
- deal_recommendation: high_intent | watch | no
- next_action: 字符串
- confidence: 0-1 数字
- data_quality: high | medium | low
- citations: [{ claim, source_url, source_snippet }]
- catalog_version_note: 字符串（重复写入用户提供的目录版本前缀便于审计）
"""
    user = f"""请基于以下信息输出上述 JSON。

【我方产品目录摘要】
{catalog_block}

【我方知识包摘要】
{kb_block}

【客户公司】
company_name: {company_name}
website: {website or "unknown"}
country_region: {country_region or "unknown"}
target_products: {target_products or "未指定"}
notes: {notes or "无"}
catalog_version_note 请填写: {catalog_version_note_hint or "unknown"}

【合并证据（含官网抓取与人工粘贴；可能为空）】
{merged_evidence or "（无证据：仅可按名称/URL 做极低置信推断）"}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def validate_eval(obj: dict[str, Any]) -> None:
    jsonschema.validate(instance=obj, schema=_eval_schema())


def run_llm_eval(
    *,
    merged_evidence: str,
    company_name: str,
    website: str,
    country_region: str,
    target_products: str,
    notes: str,
    catalog_path: Path = DEFAULT_CATALOG_PATH,
    kb_path: Path = DEFAULT_KB_PATH,
    catalog_max_chars: int = 24000,
    model: str | None = None,
) -> dict[str, Any]:
    if not catalog_path.is_file():
        raise FileNotFoundError(f"缺少 catalog: {catalog_path}")
    catalog = load_json(catalog_path)
    catalog_block = compact_catalog_for_prompt(catalog, catalog_max_chars)
    ver = str(catalog.get("catalog_version", ""))
    kb_block = ""
    if kb_path.is_file():
        kb_block = kb_prompt_block(load_json(kb_path))
    messages = build_messages(
        catalog_block=catalog_block,
        kb_block=kb_block,
        company_name=company_name,
        website=website,
        country_region=country_region,
        target_products=target_products,
        notes=notes,
        merged_evidence=merged_evidence,
        catalog_version_note_hint=ver,
    )
    raw = chat_json(messages, model=model or default_model())
    validate_eval(raw)
    return raw
