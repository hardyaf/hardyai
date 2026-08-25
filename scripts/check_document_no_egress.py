#!/usr/bin/env python3
"""Fail unless public DNS, direct IPv4, and direct IPv6 are unavailable."""

from __future__ import annotations

import json
import socket


def _dns_blocked() -> bool:
    try:
        socket.getaddrinfo("example.com", 443)
    except OSError:
        return True
    return False


def _connect_blocked(address: str, family: socket.AddressFamily) -> bool:
    target = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
    stream = socket.socket(family, socket.SOCK_STREAM)
    stream.settimeout(2)
    try:
        stream.connect(target)
    except OSError:
        return True
    finally:
        stream.close()
    return False


def main() -> int:
    result = {
        "dns_blocked": _dns_blocked(),
        "direct_ipv4_blocked": _connect_blocked("1.1.1.1", socket.AF_INET),
        "direct_ipv6_blocked": _connect_blocked("2606:4700:4700::1111", socket.AF_INET6),
    }
    result["status"] = "passed" if all(result.values()) else "failed"
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
