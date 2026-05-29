from __future__ import annotations

EVIDENCE_SEP = "\n\n---\n\n"


def merge_scrape_and_paste(
    *,
    scraped_blocks: list[tuple[str, str]],
    evidence_paste: str | None,
    max_total_chars: int = 28000,
) -> tuple[str, bool]:
    """
    scraped_blocks: (source_label, text) 如 (\"URL: https://...\", 正文)
    evidence_paste: Excel 人工粘贴列
    Returns: (merged_text, was_truncated)
    """
    parts: list[str] = []
    for label, text in scraped_blocks:
        t = (text or "").strip()
        if not t:
            continue
        parts.append(f"【{label}】\n{t}")

    paste = (evidence_paste or "").strip()
    if paste:
        parts.append("【人工粘贴 evidence_paste】\n" + paste)

    if not parts:
        return "", False

    body = EVIDENCE_SEP.join(parts)
    if len(body) <= max_total_chars:
        return body, False
    return body[:max_total_chars] + "\n\n...(证据已截断)", True
