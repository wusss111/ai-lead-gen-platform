from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

try:
    import trafilatura
except ImportError:
    trafilatura = None  # type: ignore[misc, assignment]

# Default paths to probe on the target domain
_DEFAULT_SUFFIXES = (
    "", "/about", "/about-us", "/products", "/contact", "/company",
    "/about-us/", "/pages/about-us", "/en/about", "/en/products",
    "/services", "/profile", "/history",
)

# Keywords that signal a page is likely to contain useful business info
_LINK_KEYWORDS = (
    "about", "product", "service", "contact", "company", "catalog",
    "profile", "history", "overview", "team", "factory", "quality",
    "certification", "partner", "client", "case", "solution",
    "manufacturing", "export", "trade", "wholesale", "supply",
)

USER_AGENT = (
    "Mozilla/5.0 (compatible; TradeCustomerEvalBot/1.0; +https://example.local) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_MAX_TEXT_LEN = 20000  # Fallback text extraction cap
_PARALLEL_WORKERS = 5  # Concurrent fetches per website


def _url_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]


def _normalize_url(url: str) -> str:
    """Remove trailing slash, fragment, and normalize for dedup."""
    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc.lower(), p.path.rstrip("/") or "/", "", "", ""))


def _strip_html_fallback(html: str) -> str:
    t = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    t = re.sub(r"(?is)<style.*?>.*?</style>", " ", t)
    t = re.sub(r"(?is)<[^>]+>", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:_MAX_TEXT_LEN]


def extract_text_from_html(url: str, html: str) -> str:
    if trafilatura is not None:
        txt = trafilatura.extract(html, url=url, include_comments=False, include_tables=False)
        if txt and txt.strip():
            return txt.strip()
    return _strip_html_fallback(html)


def expand_base_urls(base: str) -> list[str]:
    """Generate candidate URLs from fixed suffixes."""
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
        for suf in _DEFAULT_SUFFIXES:
            u = urljoin(seed + "/", suf.lstrip("/")) if suf else seed
            out.append(u)

    seen: set[str] = set()
    uniq: list[str] = []
    for u in out:
        nu = _normalize_url(u)
        if nu not in seen:
            seen.add(nu)
            uniq.append(u)
    return uniq


def extract_internal_links(html: str, base_url: str) -> list[str]:
    """Extract internal links from HTML, scored by business relevance."""
    base_p = urlparse(base_url)
    base_domain = base_p.netloc.lower()

    hrefs = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)

    scored: list[tuple[int, str]] = []
    seen: set[str] = set()

    for href in hrefs:
        # Resolve relative URLs
        try:
            full = urljoin(base_url, href)
        except Exception:
            continue
        p = urlparse(full)

        # Skip non-http, external domains, anchors, non-HTML
        if p.scheme not in ("http", "https"):
            continue
        if p.netloc.lower() != base_domain:
            continue
        if not p.path or p.path == "/":
            continue

        norm = _normalize_url(full)
        if norm in seen:
            continue
        seen.add(norm)

        path_lower = p.path.lower()

        # Skip clearly useless pages
        skip_patterns = ("login", "cart", "checkout", "search", "tag/", "author/",
                         "category/", "wp-content", "wp-includes", ".jpg", ".png",
                         ".pdf", ".zip", ".css", ".js", "feed", "comment", "reply")
        if any(s in path_lower for s in skip_patterns):
            continue

        # Score by keyword matches
        score = 0
        for kw in _LINK_KEYWORDS:
            if kw in path_lower:
                score += 1
        # Prefer shorter paths (more likely to be top-level pages)
        depth = path_lower.count("/")
        score -= depth * 0.5

        if score > 0:
            scored.append((score, norm))

    # Sort by score descending, take top
    scored.sort(key=lambda x: x[0], reverse=True)
    return [url for _, url in scored]


def fetch_one(
    client: httpx.Client,
    url: str,
    *,
    cache_dir: Path,
    retries: int = 2,
) -> dict[str, Any]:
    """Returns {ok, url, text, html, error, from_cache}."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _url_key(url)
    cache_txt = cache_dir / f"{key}.txt"
    cache_html = cache_dir / f"{key}.html"

    if cache_txt.is_file():
        try:
            text = cache_txt.read_text(encoding="utf-8", errors="replace")
            html = ""
            if cache_html.is_file():
                html = cache_html.read_text(encoding="utf-8", errors="replace")
            return {"ok": True, "url": url, "text": text, "html": html, "error": "", "from_cache": True}
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
                cache_html.write_text(html, encoding="utf-8")
            except OSError:
                pass
            return {"ok": True, "url": url, "text": text, "html": html, "error": "", "from_cache": False}
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(0.2 * (attempt + 1))

    return {"ok": False, "url": url, "text": "", "html": "", "error": last_err, "from_cache": False}


def _fetch_urls_parallel(
    urls: list[str],
    cache_dir: Path,
    timeout_sec: float,
    max_workers: int = _PARALLEL_WORKERS,
) -> list[dict[str, Any]]:
    """Fetch multiple URLs concurrently."""
    if not urls:
        return []

    results: list[dict[str, Any]] = []

    def _fetch_one_url(u: str) -> dict[str, Any]:
        cache_dir.mkdir(parents=True, exist_ok=True)
        key = _url_key(u)
        cache_txt = cache_dir / f"{key}.txt"
        if cache_txt.is_file():
            try:
                text = cache_txt.read_text(encoding="utf-8", errors="replace")
                html = ""
                cache_html = cache_dir / f"{key}.html"
                if cache_html.is_file():
                    html = cache_html.read_text(encoding="utf-8", errors="replace")
                return {"ok": True, "url": u, "text": text, "html": html, "error": "", "from_cache": True}
            except OSError:
                pass

        last_err = ""
        for attempt in range(2):
            try:
                with httpx.Client(timeout=timeout_sec, follow_redirects=True,
                                  headers={"User-Agent": USER_AGENT}) as client:
                    r = client.get(u)
                    r.raise_for_status()
                    html = r.text
                    text = extract_text_from_html(u, html)
                    try:
                        cache_txt.write_text(text, encoding="utf-8")
                        (cache_dir / f"{key}.html").write_text(html, encoding="utf-8")
                    except OSError:
                        pass
                    return {"ok": True, "url": u, "text": text, "html": html, "error": "", "from_cache": False}
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(0.15 * (attempt + 1))
        return {"ok": False, "url": u, "text": "", "html": "", "error": last_err, "from_cache": False}

    with ThreadPoolExecutor(max_workers=min(max_workers, len(urls))) as pool:
        futures = {pool.submit(_fetch_one_url, u): u for u in urls}
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                u = futures[f]
                results.append({"ok": False, "url": u, "text": "", "html": "", "error": str(e), "from_cache": False})

    return results


def fetch_pages_for_website_field(
    website_field: str,
    *,
    cache_dir: Path,
    max_pages: int = 15,
    timeout_sec: float = 15.0,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Returns (pages, errors).
    Phase 1: fetch suffix-based URLs in parallel.
    Phase 2: extract links from homepage, fetch discovered pages in parallel.
    """
    # Parse multiple URLs from the input field (separated by newlines or ;)
    base_urls: list[str] = []
    for chunk in re.split(r"[\n;]+", website_field or ""):
        u = chunk.strip()
        if u:
            base_urls.append(u)

    if not base_urls:
        return [], []

    # Use the first URL as the primary base
    primary_base = base_urls[0]
    if not primary_base.startswith(("http://", "https://")):
        primary_base = "https://" + primary_base

    cache_dir.mkdir(parents=True, exist_ok=True)

    # ---- Phase 1: fetch suffix-based URLs in parallel ----
    suffix_urls = expand_base_urls(primary_base)
    # Limit initial batch
    initial_batch = suffix_urls[:min(len(suffix_urls), max_pages)]
    initial_batch = list(dict.fromkeys(initial_batch))  # dedup preserving order

    all_pages: dict[str, dict[str, Any]] = {}  # url -> result

    phase1_results = _fetch_urls_parallel(initial_batch, cache_dir, timeout_sec)
    for r in phase1_results:
        all_pages[_normalize_url(r["url"])] = r

    # ---- Phase 2: discover links from homepage ----
    # Find the homepage result (the base URL without path)
    homepage = phase1_results[0] if phase1_results else None
    for r in phase1_results:
        p = urlparse(r["url"])
        if p.path in ("", "/") and r["ok"] and r.get("html"):
            homepage = r
            break

    discovered_urls: list[str] = []
    if homepage and homepage.get("html"):
        discovered_urls = extract_internal_links(homepage["html"], homepage["url"])

    # Filter out already fetched URLs
    new_urls: list[str] = []
    for u in discovered_urls:
        if _normalize_url(u) not in all_pages:
            new_urls.append(u)

    # Limit to remaining page budget
    remaining = max_pages - len(all_pages)
    new_urls = new_urls[:max(0, remaining)]

    # ---- Phase 3: fetch discovered links in parallel ----
    if new_urls:
        phase2_results = _fetch_urls_parallel(new_urls, cache_dir, timeout_sec)
        for r in phase2_results:
            all_pages[_normalize_url(r["url"])] = r

    # Assemble final results
    pages: list[dict[str, Any]] = []
    errors: list[str] = []
    for r in all_pages.values():
        # Strip html from final output (not needed downstream)
        out = {k: v for k, v in r.items() if k != "html"}
        pages.append(out)
        if not r["ok"]:
            errors.append(f"{r['url']}: {r['error']}")

    return pages, errors
