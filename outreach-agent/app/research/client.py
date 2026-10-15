"""Versioned research-service client with explicit local/remote execution modes."""
from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..config import load_env


class ResearchUnavailable(RuntimeError):
    pass


class NoServiceRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Service identity is explicit; do not forward its bearer token elsewhere.
        return None


urlopen = build_opener(NoServiceRedirects()).open


def service_url() -> str:
    return load_env().get("RESEARCH_SERVICE_URL", "").rstrip("/")


def request_research(operation: str, url: str, timeout: int = 8) -> dict:
    base = service_url()
    try:
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError
        parsed.port
    except ValueError:
        raise ResearchUnavailable("Invalid RESEARCH_SERVICE_URL.") from None
    env = load_env()
    headers = {"Content-Type": "application/json"}
    if env.get("RESEARCH_SERVICE_TOKEN"):
        headers["Authorization"] = "Bearer " + env["RESEARCH_SERVICE_TOKEN"]
    payload = json.dumps({"url": url, "timeout": timeout}).encode("utf-8")
    request = Request(f"{base}/v1/{operation}", data=payload, headers=headers, method="POST")
    try:
        # Inspect may fetch four pages, each with the bounded network timeout.
        with urlopen(request, timeout=timeout * (4 if operation == "inspect" else 1) + 5) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ResearchUnavailable("Research response is too large.")
            result = json.loads(raw)
    except (HTTPError, URLError, OSError, ValueError) as exc:
        # Never echo headers, tokens or remote response bodies into lead records.
        raise ResearchUnavailable("Research service is unavailable or returned invalid JSON.") from exc
    required = {"website_status", "status", "evidence"} if operation == "audit" else {"website", "pages", "contacts", "errors", "text"}
    if not isinstance(result, dict) or not required.issubset(result):
        raise ResearchUnavailable("Research service returned an invalid contract.")
    if operation == "audit" and not isinstance(result["evidence"], list):
        raise ResearchUnavailable("Research service returned invalid evidence.")
    if operation == "inspect" and any(not isinstance(result[key], list) for key in ("pages", "contacts", "errors")):
        raise ResearchUnavailable("Research service returned invalid inspection data.")
    return result
