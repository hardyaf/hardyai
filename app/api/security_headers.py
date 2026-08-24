from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path


_UI_ROOT = (Path(__file__).resolve().parents[1] / "ui").resolve()


def _inline_hashes(tag: str) -> list[str]:
    values: list[str] = []
    pattern = re.compile(rf"<{tag}(?:\s[^>]*)?>(.*?)</{tag}>", re.IGNORECASE | re.DOTALL)
    for path in sorted(_UI_ROOT.glob("*.html")):
        source = path.read_text(encoding="utf-8")
        for match in pattern.finditer(source):
            digest = hashlib.sha256(match.group(1).encode("utf-8")).digest()
            values.append(f"'sha256-{base64.b64encode(digest).decode('ascii')}'")
    return sorted(set(values))


def content_security_policy() -> str:
    script_hashes = " ".join(_inline_hashes("script"))
    style_hashes = " ".join(_inline_hashes("style"))
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' {script_hashes}".strip(),
            f"style-src 'self' {style_hashes}".strip(),
            "connect-src 'self'",
            "img-src 'self' data:",
            "object-src 'none'",
            "base-uri 'none'",
            "frame-ancestors 'none'",
            "form-action 'self'",
        ]
    )


SECURITY_HEADERS = {
    "Content-Security-Policy": content_security_policy(),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
