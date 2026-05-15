from __future__ import annotations

from typing import Any


def overall_score_computed(
    *,
    product_fit_score: int,
    capability_score: int,
    reputation_safety_score: int,
    weights: dict[str, float] | None = None,
) -> float:
    w = weights or {"product_fit": 0.45, "capability": 0.25, "reputation_safety": 0.3}
    s = (
        w["product_fit"] * float(product_fit_score)
        + w["capability"] * float(capability_score)
        + w["reputation_safety"] * float(reputation_safety_score)
    )
    return round(s, 3)


def manual_review_flag(
    *,
    overall: float,
    reputation_safety_score: int,
    reputation_concerns_text: str,
    rules: dict[str, Any] | None = None,
) -> str:
    r = rules or {
        "flag_if_overall_gte": 4.0,
        "flag_if_reputation_safety_lte": 2,
        "flag_if_concerns_keyword": ["诉讼", "欺诈", "scam", "fraud"],
    }
    if overall >= float(r["flag_if_overall_gte"]):
        return "YES"
    if reputation_safety_score <= int(r["flag_if_reputation_safety_lte"]):
        return "YES"
    low = reputation_concerns_text or ""
    for kw in r.get("flag_if_concerns_keyword", []):
        if kw and str(kw) in low:
            return "YES"
    return "NO"


def cap_model_data_quality(
    model_dq: str,
    *,
    any_fetch_ok: bool,
    paste_len: int,
) -> str:
    """程序封顶：无成功抓取且粘贴很短时，不高于 medium。"""
    order = {"low": 0, "medium": 1, "high": 2}
    inv = {0: "low", 1: "medium", 2: "high"}
    v = order.get(model_dq, 1)
    if not any_fetch_ok and paste_len < 50:
        v = min(v, 1)
    return inv[v]
