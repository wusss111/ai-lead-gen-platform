"""
使用 DeepSeek 大模型，结合 build_product_catalog 生成的 catalog.json，
对客户公司与「我方产品」做结构化契合度初评（JSON）。

依赖环境变量:
  DEEPSEEK_API_KEY  （必填）
  DEEPSEEK_MODEL    （可选，默认 deepseek-v4-flash；可改为 deepseek-v4-pro）
  DEEPSEEK_BASE_URL （可选，默认 https://api.deepseek.com）

示例:
  set DEEPSEEK_API_KEY=sk-...
  python tools/eval_company_fit.py --name "ACME Tools" --website https://example.com --evidence "对方官网简介摘录..."
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.deepseek_client import chat_json, default_model  # noqa: E402


def load_catalog(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def compact_catalog_for_prompt(data: dict, max_chars: int) -> str:
    lines: list[str] = []
    lines.append(
        f"catalog_version={data.get('catalog_version', '')} "
        f"sha256={data.get('source_sha256', '')[:16]}..."
    )
    for cat in data.get("categories", []):
        sheet = cat.get("sheet", "")
        lines.append(f"\n## 品类: {sheet}")
        for p in cat.get("products", []):
            model = p.get("model", "")
            feat = (p.get("features") or "").replace("\n", " ")
            if len(feat) > 220:
                feat = feat[:220] + "…"
            lines.append(f"- {model}" + (f" | {feat}" if feat else ""))
    text = "\n".join(lines)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n...(目录已截断，请提高 --catalog-max-chars 或缩小品类范围)"


def build_messages(
    *,
    catalog_block: str,
    company_name: str,
    website: str,
    country_region: str,
    target_products: str,
    notes: str,
    evidence: str,
) -> list[dict[str, str]]:
    system = """你是外贸 B2B 初筛助手。请只根据「用户给出的证据文本」与「我方产品目录摘要」进行推理。
规则:
1) 输出必须是合法 JSON 对象（不要 markdown 代码围栏）。
2) 每条关键判断尽量带依据；若无公开依据请写 "unknown" 或空数组，并降低 confidence。
3) 不要编造对方未在证据中出现的财务数据、员工数、认证编号等。
4) prompt 中已要求 json 输出 —— 严格遵守下方 JSON 字段名与类型。

JSON 字段说明（全部必填）:
- product_fit_score: 整数 1-5，与我方目录中品类/档次的匹配程度
- product_fit_reasons: 字符串数组，每条一句，可引用证据中的事实
- capability_signals: 字符串数组，仅写证据中可见的实力/规模信号
- reputation_risk: 对象，含 facts(字符串数组,有来源的事实)、concerns(字符串数组,风险或疑点)、sources(字符串数组,URL 或「证据内摘录」)
- deal_recommendation: 枚举之一: "high_intent" | "watch" | "no"
- next_action: 字符串，建议下一步动作（中文即可）
- overall_score: 整数 1-5，综合初筛分（可略低于 product_fit_score 若证据不足）
- confidence: 数字 0-1，你对本评估整体置信度
- data_quality: 枚举之一: "high" | "medium" | "low"，表示用户所给证据是否足以支撑结论
- catalog_version_note: 字符串，重复写入用户提供的 catalog_version 行中的版本前缀便于审计
"""
    user = f"""请基于以下信息输出上述 JSON。

【我方产品目录摘要】
{catalog_block}

【客户公司】
company_name: {company_name}
website: {website or "unknown"}
country_region: {country_region or "unknown"}
target_products(用户关心的对齐维度): {target_products or "未指定，请结合目录自行判断最相关品类"}
notes: {notes or "无"}

【证据文本（可能来自官网摘录、邮件、你方备注等；若很短则 data_quality 应为 low）】
{evidence or "（未提供证据，仅可按官网 URL 与名称做极低置信推断，并显式说明）"}
"""
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def main() -> None:
    ap = argparse.ArgumentParser(description="DeepSeek + 我方 catalog.json 客户契合度 JSON 评估")
    ap.add_argument("--catalog", type=Path, default=ROOT / "output" / "catalog.json")
    ap.add_argument("--catalog-max-chars", type=int, default=28000)
    ap.add_argument("--name", required=True, help="客户公司名")
    ap.add_argument("--website", default="", help="官网 URL")
    ap.add_argument("--country", default="", help="国家/地区")
    ap.add_argument("--target-products", default="", dest="target_products", help="希望对齐的品类/关键词")
    ap.add_argument("--notes", default="", help="内部备注")
    ap.add_argument("--evidence-file", type=Path, default=None, help="证据文本文件（utf-8）")
    ap.add_argument("--evidence", default="", help="证据文本（命令行直接传，较短时用）")
    ap.add_argument("--out", type=Path, default=None, help="将 JSON 结果写入该路径")
    ap.add_argument("--model", default=None, help="覆盖环境变量 DEEPSEEK_MODEL")
    args = ap.parse_args()

    if not args.catalog.is_file():
        ap.error(f"找不到 catalog: {args.catalog}，请先运行 python tools/build_product_catalog.py")

    data = load_catalog(args.catalog)
    catalog_block = compact_catalog_for_prompt(data, args.catalog_max_chars)

    evidence = args.evidence
    if args.evidence_file:
        evidence = args.evidence_file.read_text(encoding="utf-8")

    messages = build_messages(
        catalog_block=catalog_block,
        company_name=args.name,
        website=args.website,
        country_region=args.country,
        target_products=args.target_products,
        notes=args.notes,
        evidence=evidence,
    )

    model = args.model or default_model()
    try:
        result = chat_json(messages, model=model)
    except Exception as e:
        print(f"调用失败: {e}", file=sys.stderr)
        sys.exit(1)

    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"已写入 {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
