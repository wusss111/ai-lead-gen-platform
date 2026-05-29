from __future__ import annotations

from pathlib import Path

from tools.pipeline.fetch_cache import expand_base_urls, extract_text_from_html, fetch_pages_for_website_field


def test_expand_urls_adds_common_paths() -> None:
    u = expand_base_urls("example.com")
    assert any("/about" in x for x in u)
    assert u[0].startswith("https://example.com")


def test_extract_text_from_html_minimal() -> None:
    html = "<html><body><p>Hello  World</p></body></html>"
    t = extract_text_from_html("https://example.com", html)
    assert "Hello" in t


def test_fetch_example_com(tmp_path: Path) -> None:
    pages, errs = fetch_pages_for_website_field("https://example.com", cache_dir=tmp_path, max_pages=2)
    assert any(p.get("ok") for p in pages), (pages, errs)
