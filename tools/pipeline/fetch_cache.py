from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

try:
    import trafilatura
except ImportError:  # pragma: no cover
    trafilatura = None  # type: ignore[misc, assignment]

DEFAULT_SUFFIXES = ("", "/about", "/about-us", "/products", "/contact", "/company")

USER_AGENT = (
    "Mozilla/5.0 (compatible; TradeCustomerEvalBot/1.0; +https://example.local) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _url_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def _strip_html_fallback(html: str) -> str:
    t = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    t = re.sub(r"(?is)<style.*?>.*?</style>", " ", t)
    t = re.sub(r"(?is)<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:8000]


def extract_text_from_html(url: str, html: str) -> str:
    if trafilatura is not None:
        txt = trafilatura.extract(html, url=url, include_comments=False, include_tables=False)
        if txt and txt.strip():
            return txt.strip()
    return _strip_html_fallback(html)


def expand_urls(base: str, suffixes: tuple[str, ...] = DEFAULT_SUFFIXES) -> list[str]:
    base = (base or "").strip()
    if not base:
        return []
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    p = urlparse(base)
    if not p.netloc:
        return []
    root = f"{p.scheme}://{p.netloc}".rstrip("/")
    seeds: list[str] = [root]
    full = base.rstrip("/")
    if full != root:
        seeds.insert(0, full)

    out: list[str] = []
    for seed in seeds:
        for suf in suffixes:
            u = urljoin(seed + "/", suf.lstrip("/")) if suf else seed
            out.append(u)
    seen: set[str] = set()
    uniq: list[str] = []
    for u in out:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def fetch_one(
    client: httpx.Client,
    url: str,
    *,
    cache_dir: Path | None,
    retries: int = 3,
) -> dict[str, Any]:
    """返回 {ok, url, text, error, from_cache}"""
    cache_dir = cache_dir or Path(".cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _url_key(url)
    cache_txt = cache_dir / f"{key}.txt"
    cache_meta = cache_dir / f"{key}.meta.json"
    if cache_txt.is_file():
        try:
            text = cache_txt.read_text(encoding="utf-8", errors="replace")
            return {"ok": True, "url": url, "text": text, "error": "", "from_cache": True}
        except OSError:
            pass

    last_err = ""
    for attempt in range(retries):
        try:
            r = client.get(url)
            r.raise_for_status()
            html = r.text
            text = extract_text_from_html(url, html)
            try:
                cache_txt.write_text(text, encoding="utf-8")
                cache_meta.write_text(
                    json.dumps({"url": str(url)}),
                    encoding="utf-8",
                )
            except OSError:
                pass
            return {"ok": True, "url": url, "text": text, "error": "", "from_cache": False}
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(0.4 * (attempt + 1))

    return {"ok": False, "url": url, "text": "", "error": last_err, "from_cache": False}


def fetch_pages_for_website_field(
    website_field: str,
    *,
    cache_dir: Path,
    max_pages: int = 6,
    timeout_sec: float = 20.0,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    返回 (pages, errors)。每个 page: ok, url, text, error, from_cache
    """
    urls: list[str] = []
    for chunk in re.split(r"[\n;]+", website_field or ""):
        u = chunk.strip()
        if u:
            urls.extend(expand_urls(u))
    urls = urls[:max_pages]
    if not urls:
        return [], []

    cache_dir.mkdir(parents=True, exist_ok=True)
    pages: list[dict[str, Any]] = []
    errors: list[str] = []
    with httpx.Client(
        timeout=timeout_sec,
        follow_redirects=True,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        for u in urls:
            res = fetch_one(client, u, cache_dir=cache_dir, retries=3)
            pages.append(res)
            if not res["ok"]:
                errors.append(f"{u}: {res['error']}")
    return pages, errors
