from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit, urlunsplit


class UrlPolicyError(ValueError):
    pass


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}".lower()


def validate_public_url(url: str, allowed_test_origins: frozenset[str] = frozenset()) -> str:
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    p = urlsplit(url)
    if p.scheme not in ("http", "https"):
        raise UrlPolicyError(f"scheme not allowed: {p.scheme}")
    if p.username or p.password:
        raise UrlPolicyError("userinfo not allowed")
    if not p.hostname:
        raise UrlPolicyError("missing host")
    norm = urlunsplit((p.scheme, p.netloc.lower(), p.path or "/", p.query, ""))
    if _origin(norm) in allowed_test_origins:
        return norm
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror as e:
        raise UrlPolicyError(f"cannot resolve {p.hostname}") from e
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise UrlPolicyError(f"non-public address for {p.hostname}")
    return norm
