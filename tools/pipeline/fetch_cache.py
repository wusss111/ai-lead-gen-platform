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
    from curl_cffi import requests as curl_requests
    _HAS_CURL_CFFI = True
except ImportError:
    curl_requests = None  # type: ignore[assignment]
    _HAS_CURL_CFFI = False

try:
    import trafilatura
except ImportError:
    trafilatura = None  # type: ignore[misc, assignment]

# 常见页面后缀（精简：避免 Shopify 等动态站点返回假 200 造成浪费）
_DEFAULT_SUFFIXES = (
    "", "/about", "/products", "/contact", "/contact-us",
    "/about-us", "/support",
)

# Keywords that signal a page is likely to contain useful business info
_LINK_KEYWORDS = (
    "about", "product", "service", "contact", "company", "catalog",
    "profile", "history", "overview", "team", "factory", "quality",
    "certification", "partner", "client", "case", "solution",
    "manufacturing", "export", "trade", "wholesale", "supply",
)

# 完整浏览器请求头，避免被 WAF 拦截
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
    # 注意：不设 Accept-Encoding，让 httpx 自行处理（httpx 不支持 br 解压，设了反而拿乱码）
    "Cache-Control": "no-cache",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

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


def _try_curl_cffi_fetch(url: str, timeout_sec: float) -> tuple[str, str] | None:
    """用 curl_cffi（Chrome TLS 指纹）抓取，突破 Cloudflare。返回 (html, text) 或 None。"""
    if not _HAS_CURL_CFFI:
        return None
    try:
        r = curl_requests.get(
            url,
            impersonate="chrome124",
            headers={
                "Accept": BROWSER_HEADERS["Accept"],
                "Accept-Language": BROWSER_HEADERS["Accept-Language"],
            },
            timeout=timeout_sec,
        )
        if r.status_code == 200:
            html = r.text
            text = extract_text_from_html(url, html)
            return html, text
    except Exception:
        pass
    return None


import threading as _threading
_pw_lock = _threading.Lock()

def _try_playwright_fetch(url: str, timeout_sec: float) -> tuple[str, str] | None:
    """用 Playwright 无头浏览器渲染 JavaScript 后抓取。返回 (html, text) 或 None。
    仅当静态抓取未找到邮箱时触发（开销大，作为最后兜底）。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    if not _pw_lock.acquire(timeout=timeout_sec):
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=min(timeout_sec * 1000, 30000), wait_until="domcontentloaded")
            page.wait_for_timeout(3000)  # 等 JS 渲染完成
            html = page.content()
            browser.close()
            text = extract_text_from_html(url, html)
            return html, text
    except Exception:
        return None
    finally:
        _pw_lock.release()


def _playwright_discover_contact_pages(base_url: str, timeout_sec: float = 15.0) -> list[dict[str, Any]]:
    """用 Playwright 渲染首页 → 提取 JS 动态链接 → 访问联系页面 → 提取邮箱。
    处理 Shopify/Wix/SPA 等静态抓取看不到链接的站点。
    返回与 fetch_pages 兼容的 list[dict]。
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []

    if not _pw_lock.acquire(timeout=timeout_sec):
        return []

    results: list[dict[str, Any]] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(base_url, timeout=min(timeout_sec * 1000, 30000), wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # 提取渲染后的所有内部链接
            links = page.evaluate("""() => {
                const base = new URL(window.location.origin);
                return Array.from(document.querySelectorAll('a[href]'))
                    .map(a => ({href: a.href, text: (a.textContent || '').trim()}))
                    .filter(l => {
                        try { const u = new URL(l.href); return u.hostname === base.hostname; }
                        catch { return false; }
                    });
            }""")

            # 找联系相关页面（按关键词优先级排序）
            contact_keywords = ["contact", "about", "email", "support", "help",
                              "reach", "inquiry", "get-in-touch", "location", "team"]
            scored_links: list[tuple[int, str]] = []
            seen: set[str] = set()
            for l in links:
                href = l["href"]
                if href in seen:
                    continue
                seen.add(href)
                score = 0
                lower = (l["text"] + " " + href).lower()
                for i, kw in enumerate(contact_keywords):
                    if kw in lower:
                        score += len(contact_keywords) - i
                        break
                if score > 0:
                    scored_links.append((score, href))
            scored_links.sort(key=lambda x: x[0], reverse=True)

            # 访问前 3 个联系页面
            for _, contact_url in scored_links[:3]:
                try:
                    cp = browser.new_page()
                    cp.goto(contact_url, timeout=20000, wait_until="domcontentloaded")
                    cp.wait_for_timeout(2000)
                    html = cp.content()
                    cp.close()
                    text = extract_text_from_html(contact_url, html)
                    results.append({"ok": True, "url": contact_url, "text": text,
                                   "html": html, "error": "", "from_cache": False})
                except Exception:
                    pass

            # 首页本身也加入结果
            html = page.content()
            text = extract_text_from_html(base_url, html)
            results.insert(0, {"ok": True, "url": base_url, "text": text,
                               "html": html, "error": "", "from_cache": False})
            browser.close()
    except Exception:
        pass
    finally:
        _pw_lock.release()
    return results


def _clean_error_message(err: str) -> str:
    """清理 httpx 错误消息，去掉 Mozilla 文档链接等冗余信息。"""
    # 去掉 "For more information check: https://developer.mozilla.org/..."
    err = re.sub(r"\s*For more information check:.*$", "", err, flags=re.IGNORECASE)
    # 截断过长的 URL（保留错误类型和状态码）
    if len(err) > 150:
        # 保留错误类型 + 状态码，去掉完整 URL
        err = re.sub(r" for url 'https?://[^']*'", "", err)
    return err.strip()[:200]


def extract_text_from_html(url: str, html: str) -> str:
    if trafilatura is not None:
        txt = trafilatura.extract(html, url=url, include_comments=False, include_tables=False)
        if txt and txt.strip():
            return txt.strip()
    return _strip_html_fallback(html)


_EMAIL_RE = re.compile(r"[a-zA-Z0-9._+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", re.IGNORECASE)
# 国际电话号码：+国家代码 空格 号码，或 00开头的国际格式
_PHONE_RE = re.compile(
    r"(?:(?:\+|00)\d{1,4}[\s\-.]?)?(?:\(?\d{2,4}\)?[\s\-.]?)?\d{2,4}[\s\-.]?\d{2,4}[\s\-.]?\d{2,6}",
    re.IGNORECASE,
)
# 常见无关邮箱，这些不是目标客户的联系方式
_SKIP_EMAIL_DOMAINS = {
    "example.com", "domain.com", "test.com", "email.com", "mail.com",
    "yourcompany.com", "company.com", "website.com", "site.com",
}
# 垃圾邮箱关键词——包含这些词的邮箱多半是举报/垃圾/不回信的
_SPAM_EMAIL_KEYWORDS = (
    "fraud", "spam", "abuse", "noreply", "no-reply", "no_reply",
    "donotreply", "do-not-reply", "phishing", "report", "complaints",
    "legal", "dmca", "privacy", "security",
)
# 通用客服/部门邮箱前缀——大公司通常用来接收客户问询，不是采购决策人
_GENERIC_LOCAL_PARTS = {
    "customers", "customer", "customerservice", "customer_service", "customer-care",
    "customercare", "customer.support", "customersupport",
    "enquiries", "enquiry", "enquire", "enquires",
    "queries", "query",
    "admin", "administrator", "webmaster", "postmaster", "hostmaster",
    "mail", "mailbox", "inbox", "office",
    "feedback", "suggestions",
    "hr", "jobs", "careers", "recruitment", "recruit",
    "help", "helpdesk", "billing", "accounts", "accounting",
    "press", "media", "marketing", "advertising",
    "orders", "order", "returns", "return",
    "support", "contactus", "contact-us", "contact_us",
    "general", "hello", "hi", "test",
}
# 注意：sales@, info@, service@, export@ 等在小公司通常是真实联系人，不过滤



def _clean_email_html_entities(email: str) -> str:
    """清理邮箱中的 HTML 实体残留和编码杂物。"""
    import html as _html
    from urllib.parse import unquote
    e = email.strip().rstrip(".").rstrip(",").rstrip(";")
    # URL-decode（%20→空格, %40→@ 等）；unquote 对纯文本安全
    e = unquote(e)
    # 去掉 URL 解码后残留的空格
    e = re.sub(r'\s+', '', e)
    # 处理十六进制 HTML 实体 &#x40; → @, &#x2E; → .
    e = re.sub(r'&#x40;|&#64;|&#x040;', '@', e, flags=re.IGNORECASE)
    e = re.sub(r'&#x2e;|&#46;|&#x02E;', '.', e, flags=re.IGNORECASE)
    # URL-encoded（unquote 可能没处理完的）
    e = re.sub(r'%40', '@', e, flags=re.IGNORECASE)
    e = re.sub(r'%2e', '.', e, flags=re.IGNORECASE)
    # 标准 HTML 实体
    e = _html.unescape(e)
    # 去掉 Unicode 控制字符和常见 artifact
    e = re.sub(r"[\x00-\x1f><\[\]\"\'\{\}]", "", e)
    # 去掉常见前缀杂物和 JS 残留
    while e and e[0] in ">;<:|. ,`+=!()":
        e = e[1:]
    while e and e[-1] in "<;:|. ,`+=!()":
        e = e[:-1]
    # JS event handler 残留
    e = re.sub(r'\bon\w+\s*=\s*["\'][^"\']*["\']', '', e, flags=re.IGNORECASE)
    e = re.sub(r'javascript\s*:\s*[^;]*;?', '', e, flags=re.IGNORECASE)
    return e.strip()


def _is_valid_email(email: str) -> bool:
    """过滤明显无效的邮箱。"""
    email = email.strip().lower()
    # 先清理 HTML 实体
    cleaned = _clean_email_html_entities(email)
    if cleaned != email.lower():
        email = cleaned
    if len(email) < 6 or len(email) > 254:
        return False
    if email.startswith("@") or email.endswith("@"):
        return False
    if ".." in email:
        return False
    if any(email.endswith(ext) for ext in (".jpg", ".png", ".gif", ".css", ".js", ".pdf")):
        return False
    # 过滤垃圾关键词
    for kw in _SPAM_EMAIL_KEYWORDS:
        if kw in email:
            return False
    domain = email.rsplit("@", 1)[-1]
    if domain in _SKIP_EMAIL_DOMAINS:
        return False
    # 过滤通用客服/部门邮箱（customers@, enquiries@, admin@ 等）
    local_part = email.split("@")[0].lower()
    if local_part in _GENERIC_LOCAL_PARTS:
        return False
    return True


def _rank_emails(emails: list[str], website_domain: str = "") -> list[str]:
    """按相关性排序邮箱：域名匹配 > mailto 链接 > sales/info/contact 关键词 > 其他。
    垃圾邮箱已在 _is_valid_email 阶段过滤掉。
    """
    def score(e: str) -> int:
        s = 0
        e_lower = e.lower()
        # 域名匹配：邮箱域名 == 网站域名
        email_domain = e_lower.rsplit("@", 1)[-1] if "@" in e_lower else ""
        if website_domain and email_domain == website_domain:
            s += 10
        elif website_domain and email_domain.endswith("." + website_domain):
            s += 8
        # 常见业务邮箱前缀
        for prefix in ("sales", "info", "contact", "hello", "support", "inquiry", "export", "trade", "marketing", "service"):
            if e_lower.startswith(prefix + "@") or e_lower.startswith(prefix + "."):
                s += 3
                break
        # 短且干净的邮箱名更可能是主邮箱
        local = e_lower.split("@")[0]
        if len(local) <= 12:
            s += 2
        return s

    # 去重并按评分降序
    unique = list(dict.fromkeys(emails))  # 保持顺序的去重
    return sorted(unique, key=score, reverse=True)


def _extract_domain_from_url(url: str) -> str:
    """从 URL 提取纯域名（小写）。"""
    if not url:
        return ""
    domain = re.sub(r'^https?://(?:www\.)?', '', url.lower().rstrip("/"))
    return domain.split("/")[0]


def _extract_from_json_ld(html: str) -> dict[str, list[str]]:
    """Extract emails and phones from JSON-LD structured data blocks."""
    emails: list[str] = []
    phones: list[str] = []

    # Match <script type="application/ld+json">...</script>
    blocks = re.findall(
        r'<script[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.IGNORECASE | re.DOTALL,
    )

    for block in blocks:
        try:
            data = json.loads(block.strip())
        except (json.JSONDecodeError, ValueError):
            continue

        _walk_json_ld(data, emails, phones)

    return {"emails": list(dict.fromkeys(emails)), "phones": list(dict.fromkeys(phones))}


def _walk_json_ld(obj: Any, emails: list[str], phones: list[str]) -> None:
    """Recursively walk JSON-LD object for email/telephone/ContactPoint fields."""
    if isinstance(obj, dict):
        # Direct email/telephone fields
        for key, val in obj.items():
            key_lower = key.lower()
            if key_lower in ("email", "emails") and isinstance(val, str):
                e = _clean_email_html_entities(val.strip().rstrip("."))
                if _is_valid_email(e) and e not in emails:
                    emails.append(e)
            elif key_lower in ("telephone", "phone", "tel", "faxnumber", "fax") and isinstance(val, str):
                if len(val.strip()) >= 7 and val not in phones:
                    phones.append(val.strip())
            elif key_lower == "contactpoint" and isinstance(val, (dict, list)):
                _walk_json_ld(val, emails, phones)
            elif isinstance(val, (dict, list)):
                _walk_json_ld(val, emails, phones)
    elif isinstance(obj, list):
        for item in obj:
            _walk_json_ld(item, emails, phones)


def _deobfuscate_text(text: str) -> str:
    """Reverse common email obfuscation techniques — conservative to avoid false positives."""
    from urllib.parse import unquote
    # 1. URL-decode common encodings（%20→空格, %40→@, %2e→. 等）
    #    只在 mailto: 链接附近做 unquote，避免把 URL 参数里的编码也解开
    text = re.sub(r'mailto:([^"\'>\s]+)', lambda m: 'mailto:' + unquote(m.group(1)), text, flags=re.IGNORECASE)
    # 2. Explicit obfuscation markers: [at], (at), {at} → @
    text = re.sub(r'\s*\[at\]\s*|\s*\(at\)\s*|\s*\{at\}\s*', '@', text, flags=re.IGNORECASE)
    # 3. Explicit obfuscation markers: [dot], (dot), {dot} → .
    text = re.sub(r'\s*\[dot\]\s*|\s*\(dot\)\s*|\s*\{dot\}\s*', '.', text, flags=re.IGNORECASE)
    # 4. HTML hex entities for @ and .
    text = re.sub(r'&#x40;|&#64;', '@', text, flags=re.IGNORECASE)
    text = re.sub(r'&#x2e;|&#46;', '.', text, flags=re.IGNORECASE)
    # 5. URL-encoded @ and . in plain text（非 mailto 上下文）
    text = re.sub(r'%40', '@', text, flags=re.IGNORECASE)
    text = re.sub(r'%2e', '.', text, flags=re.IGNORECASE)
    # 6. Remove CSS display:none spans (anti-spam)
    text = re.sub(r'<span[^>]*style\s*=\s*["\'][^"\']*display\s*:\s*none[^"\']*["\'][^>]*>.*?</span>',
                  '', text, flags=re.IGNORECASE | re.DOTALL)
    # 7. Remove common spam-traps: user@REMOVETHISdomain.com → user@domain.com
    text = re.sub(r'@(?:NOSPAM|REMOVETHIS|REMOVE|DELETE|SPAMFREE)[A-Za-z]*', '@', text)
    return text


def extract_contacts_from_html(html: str, url: str = "", website_url: str = "") -> dict[str, list[str]]:
    """从 HTML 中提取邮箱和电话号码。返回 {"emails": [...], "phones": [...]}。
    emails 已按相关性排序（域名匹配优先）。
    提取策略：JSON-LD 结构化数据 → 反混淆 → mailto 链接 → 通用正则。
    """
    # 1. 优先从 JSON-LD 结构化数据提取（在清掉 script 标签之前）
    ld_contacts = _extract_from_json_ld(html)

    # 2. 反混淆 HTML 文本
    html = _deobfuscate_text(html)

    # 3. 清理：去掉注释、script、style
    cleaned = re.sub(r"(?is)<!--.*?-->", " ", html)
    cleaned = re.sub(r"(?is)<script.*?>.*?</script>", " ", cleaned)
    cleaned = re.sub(r"(?is)<style.*?>.*?</style>", " ", cleaned)

    emails: list[str] = list(ld_contacts["emails"])
    phones: list[str] = list(ld_contacts["phones"])
    mailto_flagged: set[str] = set()

    # 4. mailto: 链接（最可能是真实联系方式）
    mailto_emails = re.findall(r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})', cleaned, re.IGNORECASE)
    for e in mailto_emails:
        e = _clean_email_html_entities(e.strip().rstrip("."))
        if _is_valid_email(e) and e not in emails:
            emails.append(e)
            mailto_flagged.add(e.lower())

    # 5. 通用邮箱正则
    found = _EMAIL_RE.findall(cleaned)
    for e in found:
        e = _clean_email_html_entities(e.strip().rstrip("."))
        if _is_valid_email(e) and e not in emails:
            emails.append(e)

    # 6. 电话号码提取
    # tel: 链接优先
    tel_matches = re.findall(r'tel:([+\d][+\d\s\-().]*)', cleaned, re.IGNORECASE)
    for t in tel_matches:
        t = t.strip()
        if len(t) >= 7:
            phones.append(t)

    # callto: 链接（Skype 等）
    callto_matches = re.findall(r'callto:([+\d][+\d\s\-().]*)', cleaned, re.IGNORECASE)
    for t in callto_matches:
        t = t.strip()
        if len(t) >= 7 and t not in phones:
            phones.append(t)

    # fax: 链接
    fax_matches = re.findall(r'fax:([+\d][+\d\s\-().]*)', cleaned, re.IGNORECASE)
    for t in fax_matches:
        t = t.strip()
        if len(t) >= 7 and t not in phones:
            phones.append(t)

    # 通用电话正则
    phone_matches = _PHONE_RE.findall(cleaned)
    for p in phone_matches:
        p = p.strip()
        digits_only = re.sub(r"\D", "", p)
        if 7 <= len(digits_only) <= 15 and p not in phones:
            phones.append(p)

    # 按相关性排序邮箱
    if emails:
        domain = _extract_domain_from_url(website_url or url)
        emails = _rank_emails(emails, domain)

    return {"emails": emails, "phones": phones[:5]}


# ── 社交媒体链接提取 ──────────────────────────────────────────────

# (platform, regex_pattern, skip_patterns)
_SOCIAL_PLATFORM_SPECS: list[tuple[str, str, list[str]]] = [
    ("facebook", r"https?://(?:www\.|m\.)?(?:facebook\.com|fb\.com)/([\w.\-]+)", ["sharer.php", "share.php", "login", "plugins", "dialog", "policies", "privacy", "help", "settings", "business", "ads", "pages/", "profile.php", "photo.php", "groups/", "events/", "watch/", "reel/"]),
    ("twitter", r"https?://(?:www\.|mobile\.)?(?:twitter\.com|x\.com)/([\w]+)", ["intent", "share", "hashtag", "search", "home", "explore", "settings", "i/"]),
    ("instagram", r"https?://(?:www\.)?instagram\.com/([\w.]+)", ["p/", "reel/", "tv/", "explore/", "about/", "accounts/", "stories/"]),
    ("linkedin", r"https?://(?:www\.)?linkedin\.com/(?:company/([\w\-]+)|in/([\w\-]+))", ["jobs/", "posts/", "feed/", "pulse/", "learning/", "events/"]),
    ("youtube", r"https?://(?:www\.)?youtube\.com/(?:@([\w\-]+)|channel/([\w\-]+)|c/([\w\-]+)|user/([\w\-]+))", ["watch", "playlist", "embed", "shorts/"]),
    ("tiktok", r"https?://(?:www\.)?tiktok\.com/@([\w.\-]+)", ["music/", "tag/", "video/", "trending"]),
    ("pinterest", r"https?://(?:www\.|[a-z]{2}\.)?pinterest\.com/([\w]+)", ["pin/", "board/"]),
]

# 社交图标 class 名称 → 平台映射
_SOCIAL_ICON_CLASSES: dict[str, str] = {
    "facebook": "facebook", "fb": "facebook",
    "twitter": "twitter", "x-corp": "twitter",
    "instagram": "instagram", "insta": "instagram",
    "youtube": "youtube", "yt": "youtube",
    "linkedin": "linkedin", "linked-in": "linkedin",
    "tiktok": "tiktok", "tik-tok": "tiktok",
    "pinterest": "pinterest",
}

# 纯文本社交 URL 模式（兜底）
_SOCIAL_TEXT_PATTERNS: list[tuple[str, str]] = [
    ("facebook", r"(?:facebook\.com|fb\.com)/([\w.\-]+)"),
    ("twitter", r"(?:twitter\.com|x\.com)/([\w]+)"),
    ("instagram", r"instagram\.com/([\w.]+)"),
    ("linkedin", r"linkedin\.com/(?:company|in)/([\w\-]+)"),
    ("youtube", r"youtube\.com/(?:@([\w\-]+)|channel/([\w\-]+)|c/([\w\-]+)|user/([\w\-]+))"),
    ("tiktok", r"tiktok\.com/@([\w.\-]+)"),
    ("pinterest", r"pinterest\.com/([\w]+)"),
]


def _clean_url_for_handle(url: str) -> str:
    """Strip query, fragment, trailing slashes, and URL-encoding from a social URL."""
    p = urlparse(url)
    clean = urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    clean = clean.rstrip("/")
    # Decode URL-encoded characters
    from urllib.parse import unquote
    return unquote(clean)


def _should_skip_social_url(url: str, skip_patterns: list[str]) -> bool:
    """Check if a social URL path contains a known non-profile pattern."""
    path_lower = urlparse(url).path.lower()
    for sp in skip_patterns:
        if sp in path_lower:
            return True
    return False


def extract_social_links_from_html(html: str) -> list[dict[str, str]]:
    """Extract social media profile links from HTML.

    返回去重后的列表，每个元素为 {"platform": str, "url": str, "handle": str}。
    同平台只保留第一个匹配项。
    """
    if not html:
        return []

    seen_platforms: set[str] = set()
    results: list[dict[str, str]] = []

    # 策略1：从 <a href> 标签提取
    hrefs = re.findall(r'href\s*=\s*["\']([^"\']+)["\']', html, re.IGNORECASE)

    for href in hrefs:
        href_clean = _clean_url_for_handle(href)
        for platform, pattern, skip_patterns in _SOCIAL_PLATFORM_SPECS:
            if platform in seen_platforms:
                continue
            m = re.search(pattern, href_clean, re.IGNORECASE)
            if not m:
                continue
            if _should_skip_social_url(href_clean, skip_patterns):
                continue
            # YouTube 有多个捕获组（@handle, channel/ID, c/name, user/name）
            if platform == "youtube":
                handle = next((g for g in m.groups() if g), "")
            # LinkedIn 有两个捕获组（company/name, in/name）
            elif platform == "linkedin":
                handle = next((g for g in m.groups() if g), "")
            else:
                handle = m.group(1)
            if handle and handle.lower() not in ("www", "http", "https"):
                seen_platforms.add(platform)
                results.append({
                    "platform": platform,
                    "url": href_clean,
                    "handle": handle,
                })
                break

    # 策略2：从社交图标 class 的父级链接补充（针对图标无文字但 href 是社交链接）
    icon_href_pattern = r'(?:class\s*=\s*["\'][^"\']*\b({})(?:\b[^"\']*)?["\'])' \
        .format("|".join(re.escape(k) for k in _SOCIAL_ICON_CLASSES))
    # 在图标元素附近找 <a href>
    icon_blocks = re.findall(
        r'<a\s[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>.*?</a>',
        html, re.IGNORECASE | re.DOTALL,
    )
    for href, platform, pattern, skip_patterns in _iter_href_platform(icon_blocks):
        if platform in seen_platforms:
            continue
        href_clean = _clean_url_for_handle(href)
        m = re.search(pattern, href_clean, re.IGNORECASE)
        if not m:
            continue
        if _should_skip_social_url(href_clean, skip_patterns):
            continue
        if platform in ("youtube", "linkedin"):
            handle = next((g for g in m.groups() if g), "")
        else:
            handle = m.group(1)
        if handle and handle.lower() not in ("www", "http", "https"):
            seen_platforms.add(platform)
            results.append({"platform": platform, "url": href_clean, "handle": handle})

    # 策略3：纯文本中匹配（兜底——部分网站社交链接不在 <a> 中）
    # 先去除 script/style 标签避免误匹配
    cleaned_text = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    cleaned_text = re.sub(r"(?is)<style.*?>.*?</style>", " ", cleaned_text)
    cleaned_text = re.sub(r"<[^>]+>", " ", cleaned_text)  # 去掉所有标签，留纯文本

    for platform, pattern in _SOCIAL_TEXT_PATTERNS:
        if platform in seen_platforms:
            continue
        m = re.search(pattern, cleaned_text, re.IGNORECASE)
        if not m:
            continue
        if platform in ("youtube", "linkedin"):
            handle = next((g for g in m.groups() if g), "")
        else:
            handle = m.group(1)
        if handle and handle.lower() not in ("www", "http", "https"):
            # 还原大概的 URL
            domain_map = {
                "facebook": "facebook.com", "twitter": "twitter.com",
                "instagram": "instagram.com", "linkedin": "linkedin.com",
                "youtube": "youtube.com", "tiktok": "tiktok.com",
                "pinterest": "pinterest.com",
            }
            dom = domain_map.get(platform, f"{platform}.com")
            seen_platforms.add(platform)
            results.append({
                "platform": platform,
                "url": f"https://{dom}/{handle}",
                "handle": handle,
            })

    return results


def _iter_href_platform(hrefs: list[str]) -> list[tuple[str, str, str, list[str]]]:
    """Yield (href, platform, pattern, skip_patterns) for each href that matches a platform."""
    out: list[tuple[str, str, str, list[str]]] = []
    for href in hrefs:
        href_lower = href.lower()
        for platform, pattern, skip_patterns in _SOCIAL_PLATFORM_SPECS:
            if re.search(pattern, href, re.IGNORECASE):
                out.append((href, platform, pattern, skip_patterns))
                break
    return out


def aggregate_social_links_from_pages(pages: list[dict]) -> list[dict[str, str]]:
    """Scan all fetched page HTMLs for social links, return deduplicated list (one per platform)."""
    all_social: dict[str, dict[str, str]] = {}
    for p in pages:
        if not p.get("ok"):
            continue
        page_html = p.get("html", "")
        if not page_html:
            continue
        links = extract_social_links_from_html(page_html)
        for link in links:
            plat = link["platform"]
            if plat not in all_social:
                all_social[plat] = link
    return list(all_social.values())


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


def extract_internal_links(html: str, base_url: str) -> list[tuple[int, str]]:
    """Extract internal links from HTML, scored by business relevance.
    Returns list of (score, url) tuples sorted by score descending.
    All internal links are kept; scoring determines fetch priority.
    """
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

        # Skip clearly useless pages (media, auth, cart, etc.)
        _skip = ("login", "cart", "checkout", "search?q=", "wp-content", "wp-includes",
                 ".jpg", ".png", ".gif", ".pdf", ".zip", ".css", ".js",
                 "feed", "comment", "replytocom", "share=", "print=")
        if any(s in path_lower for s in _skip):
            continue

        # Score by keyword matches — contact/support 页面权重最高
        score = 0.5  # base score for all remaining internal links
        for kw in _LINK_KEYWORDS:
            if kw in path_lower:
                if kw in ("contact", "support", "help"):
                    score += 4
                else:
                    score += 2
        # Prefer shorter paths (more likely top-level pages)
        depth = path_lower.strip("/").count("/")
        score -= depth * 0.3

        scored.append((score, norm))

    # Sort by score descending, keep all
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored


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
            last_err = _clean_error_message(f"{type(e).__name__}: {e}")
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
                                  headers=BROWSER_HEADERS) as client:
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
                last_err = _clean_error_message(f"{type(e).__name__}: {e}")
                # 403/连接失败时，最后一次重试用 curl_cffi（TLS 指纹伪装突破 Cloudflare）
                if attempt == 1 and "403" in last_err:
                    cffi_result = _try_curl_cffi_fetch(u, timeout_sec)
                    if cffi_result:
                        chtml, ctext = cffi_result
                        try:
                            cache_txt.write_text(ctext, encoding="utf-8")
                            (cache_dir / f"{key}.html").write_text(chtml, encoding="utf-8")
                        except OSError:
                            pass
                        return {"ok": True, "url": u, "text": ctext, "html": chtml, "error": "", "from_cache": False}
                time.sleep(0.15 * (attempt + 1))
        return {"ok": False, "url": u, "text": "", "html": "", "error": last_err, "from_cache": False}

    with ThreadPoolExecutor(max_workers=min(max_workers, len(urls))) as pool:
        futures = {pool.submit(_fetch_one_url, u): u for u in urls}
        for f in as_completed(futures):
            try:
                results.append(f.result(timeout=timeout_sec + 10))
            except Exception as e:
                u = futures[f]
                results.append({"ok": False, "url": u, "text": "", "html": "", "error": f"timeout: {e}", "from_cache": False})

    return results


def fetch_pages_for_website_field(
    website_field: str,
    *,
    cache_dir: Path,
    max_pages: int = 30,
    timeout_sec: float = 15.0,
    skip_playwright: bool = False,
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

    # ---- Phase 2: discover links from homepage + all phase-1 pages ----
    discovered_scored: list[tuple[int, str]] = []
    for r in phase1_results:
        if r["ok"] and r.get("html"):
            links = extract_internal_links(r["html"], r["url"])
            discovered_scored.extend(links)

    # Dedup and sort by score
    seen_urls: set[str] = set()
    all_seen: set[str] = {_normalize_url(r["url"]) for r in phase1_results}
    scored_sorted: list[tuple[int, str]] = []
    for score, url in discovered_scored:
        norm = _normalize_url(url)
        if norm not in seen_urls and norm not in all_seen:
            seen_urls.add(norm)
            scored_sorted.append((score, url))
    scored_sorted.sort(key=lambda x: x[0], reverse=True)

    # Pick top URLs within remaining budget
    remaining2 = max_pages - len(all_pages)
    phase2_urls = [url for _, url in scored_sorted[:max(0, remaining2)]]

    # ---- Phase 3: fetch discovered links in parallel ----
    if phase2_urls:
        phase2_results = _fetch_urls_parallel(phase2_urls, cache_dir, timeout_sec)
        for r in phase2_results:
            all_pages[_normalize_url(r["url"])] = r

        # ---- Phase 4 (depth-2): extract links from phase-2 pages ----
        depth2_scored: list[tuple[int, str]] = []
        for r in phase2_results:
            if r["ok"] and r.get("html"):
                links = extract_internal_links(r["html"], r["url"])
                depth2_scored.extend(links)

        seen2: set[str] = set()
        all_seen2: set[str] = {_normalize_url(r["url"]) for r in all_pages.values()}
        scored_depth2: list[tuple[int, str]] = []
        for score, url in depth2_scored:
            norm = _normalize_url(url)
            if norm not in seen2 and norm not in all_seen2:
                seen2.add(norm)
                scored_depth2.append((score, url))
        scored_depth2.sort(key=lambda x: x[0], reverse=True)

        remaining3 = max_pages - len(all_pages)
        depth2_urls = [url for _, url in scored_depth2[:max(0, remaining3)]]
        if depth2_urls:
            depth2_results = _fetch_urls_parallel(depth2_urls, cache_dir, timeout_sec)
            for r in depth2_results:
                all_pages[_normalize_url(r["url"])] = r

    # Assemble final results
    pages: list[dict[str, Any]] = []
    errors: list[str] = []
    extracted_contacts: dict[str, list[str]] = {"emails": [], "phones": []}

    # 收集社交媒体链接（跨页面去重）
    extracted_social: list[dict[str, str]] = []
    seen_social_platforms: set[str] = set()

    for r in all_pages.values():
        html = r.pop("html", "")
        # 从成功抓取的页面提取邮箱和电话（优先首页和 contact 页面）
        if r["ok"] and html:
            parsed = urlparse(r["url"])
            path = parsed.path.lower().rstrip("/")
            is_priority = path in ("", "/", "/contact", "/contact-us", "/about", "/about-us")
            contacts = extract_contacts_from_html(html, r["url"], primary_base)
            if contacts["emails"]:
                if is_priority:
                    extracted_contacts["emails"].extend(contacts["emails"])
                else:
                    # 非优先页面追加到末尾
                    for e in contacts["emails"]:
                        if e not in extracted_contacts["emails"]:
                            extracted_contacts["emails"].append(e)
            if contacts["phones"]:
                for p in contacts["phones"]:
                    if p not in extracted_contacts["phones"]:
                        extracted_contacts["phones"].append(p)
            # 从同一份 HTML 中提取社交媒体链接（在 html 被丢弃前）
            social_links = extract_social_links_from_html(html)
            for link in social_links:
                if link["platform"] not in seen_social_platforms:
                    seen_social_platforms.add(link["platform"])
                    extracted_social.append(link)

        pages.append(r)
        if not r["ok"]:
            errors.append(_clean_error_message(f"{r['url']}: {r['error']}"))

    # 去重
    extracted_contacts["emails"] = list(dict.fromkeys(extracted_contacts["emails"]))
    extracted_contacts["phones"] = list(dict.fromkeys(extracted_contacts["phones"]))

    # 将提取到的联系人信息附加到首页结果中
    if extracted_contacts["emails"] or extracted_contacts["phones"]:
        for p in pages:
            parsed = urlparse(p["url"])
            if parsed.path in ("", "/") and p["ok"]:
                p["extracted_contacts"] = extracted_contacts
                break
        else:
            # 如果首页抓取失败，把 contacts 附加到第一个成功的页面
            for p in pages:
                if p["ok"]:
                    p["extracted_contacts"] = extracted_contacts
                    break

    # 将提取到的社交媒体链接附加到首页结果中
    if extracted_social:
        for p in pages:
            parsed = urlparse(p["url"])
            if parsed.path in ("", "/") and p["ok"]:
                p["extracted_social"] = extracted_social
                break
        else:
            for p in pages:
                if p["ok"]:
                    p["extracted_social"] = extracted_social
                    break

    # ---- Phase 5 (fallback): 没邮箱时用 Playwright 渲染 JS + 发现联系页面 ----
    if not skip_playwright and not extracted_contacts["emails"] and pages:
        pw_pages = _playwright_discover_contact_pages(primary_base, timeout_sec)
        for pp in pw_pages:
            pp_html = pp.pop("html", "")
            if pp_html:
                contacts = extract_contacts_from_html(pp_html, pp["url"], primary_base)
                if contacts.get("emails"):
                    extracted_contacts["emails"].extend(contacts["emails"])
                if contacts.get("phones"):
                    for p in contacts["phones"]:
                        if p not in extracted_contacts["phones"]:
                            extracted_contacts["phones"].append(p)
                if contacts["emails"]:
                    pp["extracted_contacts"] = contacts
            # 去重已有的 URL
            existing_urls = {_normalize_url(p["url"]) for p in pages}
            if _normalize_url(pp["url"]) not in existing_urls:
                pages.append(pp)

    return pages, errors
