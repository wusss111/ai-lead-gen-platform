"""Generate tracking pixels for email read tracking."""

from __future__ import annotations

import os
import uuid


def generate_tracking_id() -> str:
    return str(uuid.uuid4())


def build_tracking_pixel(tracking_id: str, base_url: str | None = None) -> str:
    """Build the HTML <img> tag for email tracking.

    Args:
        tracking_id: UUID for this specific email send
        base_url: External base URL (e.g., https://example.com).
                  If None, reads from TRACKING_BASE_URL env var.
    """
    if base_url is None:
        base_url = (os.environ.get("TRACKING_BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        base_url = (os.environ.get("APP_PUBLIC_URL") or "http://localhost").rstrip("/")
    url = f"{base_url}/track/open/{tracking_id}"
    return f'<img src="{url}" width="1" height="1" alt="" style="display:none" />'


def inject_tracking_pixel(body_html: str, tracking_id: str, base_url: str | None = None) -> str:
    """Append tracking pixel to the end of HTML body, before </body> if present."""
    if not body_html:
        return body_html
    pixel_tag = build_tracking_pixel(tracking_id, base_url)
    if "</body>" in body_html:
        return body_html.replace("</body>", f"{pixel_tag}</body>")
    else:
        return body_html + pixel_tag
