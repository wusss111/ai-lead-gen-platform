"""定向导入 E:\产品图 知识库——跳过产品照片，只导入有文字的文件。"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
E_ROOT = Path("E:/产品图")

# 分层导入策略
PLAN = [
    # (目录glob, collection, 说明)
    ("**/*.pdf", "产品信息", "PDF 说明书"),
    ("**/*.doc", "产品信息", "Word 文档"),
    ("**/*.docx", "产品信息", "Word 文档"),
    ("**/*说明书*/**/*.jpg", "产品信息", "说明书扫描件（有文字）"),
    ("**/*说明书*/**/*.png", "产品信息", "说明书截图（有文字）"),
]

# XLSX 采购表（在 E:\ 根目录，不在产品图下）
XLSX_PRICE_LIST = Path("E:/2026万用表新报价系统 采购价格.xlsx")


def main():
    from tools.doc_parser import process_file
    from tools.vector_store import add_documents
    from tqdm import tqdm

    total_parents = 0
    total_children = 0

    for pattern, collection, desc in PLAN:
        files = sorted(E_ROOT.glob(pattern))
        if not files:
            print(f"[跳过] {pattern} → 0 个文件")
            continue

        print(f"\n{'='*50}")
        print(f"[{desc}] 共 {len(files)} 个文件 → collection={collection}")
        print(f"{'='*50}")

        for f in tqdm(files, desc=desc):
            try:
                results = process_file(f, collection=collection, enable_ocr_cleanup=True)
                if results:
                    n = add_documents(collection, results)
                    total_parents += len(results)
                    total_children += n
            except Exception as e:
                print(f"\n  失败: {f.name} — {e}")

    # 采购价格表（E:\ 根目录下的 XLSX）
    if XLSX_PRICE_LIST.exists():
        print(f"\n[XLSX 采购价格表] {XLSX_PRICE_LIST.name} ({XLSX_PRICE_LIST.stat().st_size/1024/1024:.0f}MB)")
        try:
            results = process_file(XLSX_PRICE_LIST, collection="采购表单", enable_ocr_cleanup=False)
            if results:
                n = add_documents("采购表单", results)
                total_parents += len(results)
                total_children += n
                print(f"  完成: {len(results)} 父文档, {n} 子文档")
        except Exception as e:
            print(f"  失败: {e}")

    # 产品目录结构入库（基于文件夹名/文件名构建产品索引）
    print(f"\n[产品目录结构] 构建中...")
    catalog = build_product_catalog()
    if catalog.strip():
        from tools.doc_parser import split_into_parents, parent_to_children
        from tools.embedding import get_embeddings
        parents = split_into_parents(catalog, source_type="catalog")
        docs = []
        for p in parents:
            children = parent_to_children(p["text"])
            docs.append({
                "parent_text": p["text"],
                "children": children,
                "metadata": {
                    "doc_title": "产品目录索引",
                    "source_file": "product_catalog.txt",
                    "collection": "产品信息",
                    "source_type": "catalog",
                },
            })
        if docs:
            n = add_documents("产品信息", docs)
            total_parents += len(docs)
            total_children += n
        print(f"  产品目录: {len(docs)} 父文档, {n} 子文档")

    print(f"\n{'='*50}")
    print(f"导入完成！父文档={total_parents}, 子文档={total_children}")
    print(f"{'='*50}")


def build_product_catalog() -> str:
    """从 E:\产品图 目录结构构建产品目录索引文本。"""
    lines = ["# 公司产品目录索引\n"]
    categories = [d for d in E_ROOT.iterdir() if d.is_dir()]

    for cat in sorted(categories):
        lines.append(f"\n## 产品分类: {cat.name}")
        models = set()

        # 从文件名提取型号
        for f in cat.rglob("*"):
            if f.is_file() and f.suffix.lower() in (".pdf", ".doc", ".docx", ".jpg", ".png"):
                # 提取目录名作为型号
                parent_dir = f.parent.name
                if parent_dir != cat.name:
                    models.add(parent_dir)
                # 从文件名提取型号（如 DT830B, RJ45-CAT6 等）
                stem = f.stem
                # 常见型号模式：字母+数字 或 数字+字母
                import re
                model_match = re.search(r'[A-Z]+[\d]+[A-Z]*[\d]*', stem.upper())
                if not model_match:
                    model_match = re.search(r'\d+[A-Z]+[\d]*', stem.upper())
                if model_match:
                    models.add(model_match.group())

        if models:
            lines.append(f"产品型号: {', '.join(sorted(models)[:30])}")

        # 列出子目录
        subdirs = [d.name for d in cat.iterdir() if d.is_dir()]
        if subdirs:
            lines.append(f"产品系列: {', '.join(subdirs[:20])}")

    return "\n".join(lines)


if __name__ == "__main__":
    main()
