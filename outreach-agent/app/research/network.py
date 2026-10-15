"""Public HTTP(S) fetches with private-address checks, including redirect targets."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, build_opener


def validate_public_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Only public HTTP(S) URLs without credentials can be researched.")
    addresses = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError("Private and special network addresses cannot be researched.")


class PublicRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_public(request, timeout: int):
    validate_public_url(request.full_url)
    return build_opener(PublicRedirectHandler()).open(request, timeout=timeout)
