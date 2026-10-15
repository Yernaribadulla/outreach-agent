from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin, urlparse
from urllib.request import Request
from ..research.network import open_public

EMAIL_RE = re.compile(r"(?i)\b[a-z0-9._%+\-]+@(?!example\.)[a-z0-9.\-]+\.[a-z]{2,}\b")
GENERIC_PREFIXES = ("info", "contact", "hello", "clinic", "admin", "booking", "office", "reception")


def fetch(url: str, timeout: int = 12) -> tuple[str, str]:
    req = Request(url, headers={"User-Agent": "B2B-Outreach-Research/1.0"})
    with open_public(req, timeout=timeout) as response:
        content_type = response.headers.get("content-type", "")
        if "text/html" not in content_type: return "", response.geturl()
        return response.read(500_000).decode("utf-8", errors="ignore"), response.geturl()


def visible_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).strip()[:6000]


def extract_business_contacts(html: str, source_url: str) -> list[dict[str, str]]:
    found = sorted({m.lower() for m in EMAIL_RE.findall(html)})
    result = []
    for email in found:
        prefix = email.split("@", 1)[0]
        kind = "generic_business" if prefix in GENERIC_PREFIXES else "public_business"
        result.append({"email": email, "source_url": source_url, "kind": kind})
    return result


def contact_urls(website: str) -> list[str]:
    base = website if website.startswith("http") else "https://" + website
    parsed = urlparse(base)
    root = f"{parsed.scheme}://{parsed.netloc}"
    return [base, urljoin(root, "/contact"), urljoin(root, "/about"), urljoin(root, "/contact-us")]


def inspect_site_local(website: str, timeout: int = 12) -> dict:
    pages = []; errors = []
    for url in contact_urls(website):
        try:
            html, final_url = fetch(url, timeout=timeout)
            if html: pages.append({"url": final_url, "text": visible_text(html), "contacts": extract_business_contacts(html, final_url)})
        except Exception as exc:
            errors.append({"url": url, "error": str(exc)[:160]})
        if pages and len(pages) >= 3: break
    contacts = []
    seen = set()
    for page in pages:
        for contact in page["contacts"]:
            if contact["email"] not in seen: contacts.append(contact); seen.add(contact["email"])
    return {"website": website, "pages": pages, "contacts": contacts, "errors": errors, "text": "\n".join(p["text"] for p in pages)[:12000]}


def inspect_site(website: str) -> dict:
    from ..research.client import ResearchUnavailable, request_research, service_url
    if not service_url():
        return inspect_site_local(website)
    try:
        return request_research("inspect", website, timeout=12)
    except ResearchUnavailable:
        return {"website": website, "pages": [], "contacts": [], "text": "",
                "errors": [{"url": website, "error": "Research service is unavailable."}]}
