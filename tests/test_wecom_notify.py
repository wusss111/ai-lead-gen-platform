def test_wecom_config_from_env_empty():
    from tools.wecom_notify import WeComConfig
    import os
    saved = {k: os.environ.pop(k, None) for k in list(os.environ) if k.startswith("WECOM_")}
    try:
        cfg = WeComConfig.from_env()
        assert cfg.corp_id == ""
        assert cfg.agent_id == ""
    finally:
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v


def test_send_reply_card_not_configured():
    """Should return False without crashing when WeCom is not configured."""
    from tools.wecom_notify import send_reply_card, WeComConfig
    cfg = WeComConfig(corp_id="", agent_id="", agent_secret="")
    result = send_reply_card(
        wework_userid="test",
        customer_name="Test Corp",
        original_snippet="Hello",
        draft_snippet="Hi there",
        draft_id=1,
        cfg=cfg,
    )
    assert result is False
