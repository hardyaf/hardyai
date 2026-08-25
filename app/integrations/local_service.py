from __future__ import annotations

import ipaddress
from urllib.parse import urlparse


def validate_local_http_service_url(value: str, *, label: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{label} must be an absolute HTTP(S) URL")
    hostname = parsed.hostname.casefold()
    local = hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".local")
    if not local:
        try:
            address = ipaddress.ip_address(hostname)
            local = address.is_private or address.is_loopback or address.is_link_local
        except ValueError:
            local = "." not in hostname
    if not local:
        raise ValueError(f"{label} must resolve through a local/private service name")
    return normalized
