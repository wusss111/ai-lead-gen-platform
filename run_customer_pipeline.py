#!/usr/bin/env python3
"""CLI：读入客户 Excel，抓取（可选）、合并 evidence_paste、调用 DeepSeek 写回结果。

示例（无密钥、不抓站点的冒烟）:
  python run_customer_pipeline.py --input examples/demo_input.xlsx --output examples/demo_output.xlsx --dry-run --no-fetch

完整评估需设置 DEEPSEEK_API_KEY，并准备 output/catalog.json（见 tools/build_product_catalog.py）。
主表 Summary 导出列由 schemas/excel_io.json 的 summary_export_columns 定义（含产品匹配分、综合分等；综合分列在最后）。
默认可写入 Detail（合并证据与 eval_json）；可用 --no-detail / --no-highlight 关闭。
加 --no-fetch 则不访问客户网站（更快）；notes / evidence_paste 仍会并入模型输入。
环境变量：CATALOG_PATH、PRODUCT_KB_PATH、CACHE_DIR、PIPELINE_CONFIG_PATH（JSON 覆盖权重与复核规则）。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.pipeline.runner import run_pipeline  # noqa: E402


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="外贸客户初筛流水线（Excel）")
    ap.add_argument("--input", type=Path, required=True, help="输入 .xlsx")
    ap.add_argument("--output", type=Path, required=True, help="输出 .xlsx")
    ap.add_argument("--dry-run", action="store_true", help="跳过 LLM，用于连通性/列校验")
    ap.add_argument("--no-fetch", action="store_true", help="不发起 HTTP 抓取")
    ap.add_argument("--limit", type=int, default=None, help="本段最多处理 N 行（与 --start-row 联用可分批）")
    ap.add_argument("--start-row", type=int, default=0, help="从 0 计的起始行索引（分批续跑）")
    ap.add_argument(
        "--append-output",
        action="store_true",
        help="若输出文件已存在则追加 Summary/Detail（需与 --start-row 等配合）",
    )
    ap.add_argument("--cache-dir", type=Path, default=None, help="抓取缓存目录")
    ap.add_argument("--catalog", type=Path, default=None, help="覆盖 catalog.json 路径")
    ap.add_argument("--kb", type=Path, default=None, help="覆盖 product_kb kb.json 路径")
    ap.add_argument("--excel-io", type=Path, default=None, help="覆盖 schemas/excel_io.json")
    ap.add_argument(
        "--pipeline-config",
        type=Path,
        default=None,
        help="JSON 覆盖权重/复核规则（也可用环境变量 PIPELINE_CONFIG_PATH）",
    )
    ap.add_argument("--no-detail", action="store_true", help="不写入 Detail 工作表")
    ap.add_argument("--no-highlight", action="store_true", help="不对 manual_review_flag=YES 行着色")
    ap.add_argument("--stop-on-error", action="store_true", help="任一行 LLM 失败则终止并抛出异常")
    args = ap.parse_args()

    run_pipeline(
        args.input,
        args.output,
        dry_run=args.dry_run,
        no_fetch=args.no_fetch,
        limit=args.limit,
        start_row=args.start_row,
        append_output=args.append_output,
        cache_dir=args.cache_dir,
        catalog_path=args.catalog,
        kb_path=args.kb,
        excel_io_path=args.excel_io,
        pipeline_config_path=args.pipeline_config,
        detail_sheet=not args.no_detail,
        highlight_manual_review=not args.no_highlight,
        stop_on_error=args.stop_on_error,
    )
    print(f"已写入: {args.output}")


if __name__ == "__main__":
    main()
