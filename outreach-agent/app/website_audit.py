from __future__ import annotations

import re
import time
from html import unescape
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


def _fetch(url: str, timeout: int = 12) -> tuple[str, str, int, float]:
    started = time.perf_counter(); req = Request(url, headers={"User-Agent": "B2B-Lead-Research/1.0"})
    with urlopen(req, timeout=timeout) as response:
        data = response.read(500_000).decode("utf-8", errors="ignore")
        return data, response.geturl(), getattr(response, "status", 200), round(time.perf_counter() - started, 3)


def audit_website(url: str | None, timeout: int = 8) -> dict:
    if not url: return {"website_status": "NO_WEBSITE_FOUND", "status": "UNKNOWN", "evidence": [], "source": None}
    target = url if url.startswith("http") else "https://" + url
    try: html, final_url, http_status, response_time = _fetch(target, timeout=timeout)
    except Exception as exc:
        return {"website_status": "WEBSITE_UNAVAILABLE", "status": "CONFIRMED", "url": target, "error": str(exc)[:200], "evidence": [{"status": "CONFIRMED", "fact": "Website request failed", "source": target, "confidence": "HIGH"}]}
    title = unescape(re.search(r"(?is)<title[^>]*>(.*?)</title>", html).group(1)).strip() if re.search(r"(?is)<title[^>]*>(.*?)</title>", html) else ""
    meta = re.search(r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', html)
    internal = {urljoin(final_url, href).split("#", 1)[0] for href in re.findall(r'(?is)href=["\']([^"\']+)', html) if urlparse(urljoin(final_url, href)).netloc == urlparse(final_url).netloc}
    text = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html)
    evidence = [{"status": "CONFIRMED", "fact": "Website responds", "source": final_url, "confidence": "HIGH"}, {"status": "CONFIRMED", "fact": f"HTTP {http_status}", "source": final_url, "confidence": "HIGH"}]
    if not title: evidence.append({"status": "CONFIRMED", "fact": "Title metadata not detected", "source": final_url, "confidence": "HIGH"})
    if not meta: evidence.append({"status": "CONFIRMED", "fact": "Meta description not detected", "source": final_url, "confidence": "HIGH"})
    signals = {"mobile_friendly": bool(re.search(r"name=[\"']viewport[\"']", html, re.I)), "booking": bool(re.search(r"(book|booking|запис|appointment)", text, re.I)), "whatsapp": "wa.me" in html.lower() or "whatsapp" in text.lower(), "forms": bool(re.search(r"<form\b", html, re.I)), "online_payment": bool(re.search(r"(payment|оплат|kaspi|paybox)", text, re.I)), "chat": bool(re.search(r"(tawk|jivo|chatra|intercom|chat-widget)", html, re.I))}
    missing_quality = int(not title) + int(not meta) + int(not signals["mobile_friendly"])
    status = "WEBSITE_WEAK" if missing_quality >= 2 else "WEBSITE_OK"
    return {"website_status": status, "status": "CONFIRMED", "url": final_url, "http_status": http_status, "https": final_url.startswith("https://"), "response_time": response_time, "title": title, "meta_description": unescape(meta.group(1)).strip() if meta else "", "internal_links": len(internal), "page_size": len(html.encode("utf-8")), "signals": signals, "evidence": evidence, "source": final_url}
