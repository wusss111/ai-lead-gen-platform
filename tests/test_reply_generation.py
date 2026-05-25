"""Tests for AI reply generation (require DeepSeek API — run manually)."""

import pytest


@pytest.mark.skip(reason="需要 DeepSeek API，仅在本地手动运行时取消 skip")
def test_generate_reply_basic():
    from tools.email_generator import generate_reply
    result = generate_reply(
        original_subject="Re: Product inquiry from ABC Corp",
        original_body="We are interested in your LED products. Can you send catalog and pricing?",
        original_from="john@abccorp.com",
        customer_context="company_name: ABC Corp\ncountry_region: US\nproduct_fit_reasons: LED lighting需求匹配",
        from_name="张三",
    )
    assert isinstance(result, dict)
    assert "subject" in result
    assert "body_text" in result
    assert result["subject"].startswith("Re:")


@pytest.mark.skip(reason="需要 DeepSeek API，仅在本地手动运行时取消 skip")
def test_generate_reply_handles_empty_context():
    from tools.email_generator import generate_reply
    result = generate_reply(
        original_subject="Hello",
        original_body="I want to buy from you.",
        original_from="test@test.com",
        from_name="test",
    )
    assert isinstance(result, dict)
    assert "body_text" in result


def test_generate_reply_with_error():
    """模拟 API 错误场景（不依赖真实 API）。"""
    from tools.email_generator import generate_reply
    result = generate_reply(
        original_subject="Test",
        original_body="Test body.",
        original_from="a@b.com",
        from_name="test",
        model="nonexistent-model-xyz",  # 触发 API 错误
    )
    assert isinstance(result, dict)
    # 即使失败也返回合法结构
    assert "subject" in result
    assert "body_text" in result
    assert "tone" in result
    assert "needs_human_input" in result


def test_build_customer_context_no_row():
    """查询不存在的 customer_id 应返回空字符串。"""
    from tools.email_generator import _build_customer_context
    result = _build_customer_context(-99999)
    assert result == ""
