from __future__ import annotations

import re
import time
from datetime import datetime, timezone
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
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try: html, final_url, http_status, response_time = _fetch(target, timeout=timeout)
    except Exception as exc:
        return {"website_status": "WEBSITE_UNAVAILABLE", "status": "UNKNOWN", "url": target, "error": str(exc)[:200], "evidence": [{"status": "UNKNOWN", "fact": "Website could not be inspected", "snippet": str(exc)[:200], "detection_reason": "The HTTP request failed; no feature conclusion can be drawn.", "source": target, "confidence": "LOW", "observed_at": observed_at}]}
    title = unescape(re.search(r"(?is)<title[^>]*>(.*?)</title>", html).group(1)).strip() if re.search(r"(?is)<title[^>]*>(.*?)</title>", html) else ""
    meta = re.search(r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', html)
    internal = {urljoin(final_url, href).split("#", 1)[0] for href in re.findall(r'(?is)href=["\']([^"\']+)', html) if urlparse(urljoin(final_url, href)).netloc == urlparse(final_url).netloc}
    text = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html)
    evidence = [{"status": "CONFIRMED", "fact": "Website responds", "source": final_url, "confidence": "HIGH", "observed_at": observed_at}, {"status": "CONFIRMED", "fact": f"HTTP {http_status}", "source": final_url, "confidence": "HIGH", "observed_at": observed_at}]
    if not title: evidence.append({"status": "NOT_DETECTED", "fact": "Title metadata not detected on the reviewed page", "snippet": "<head>", "source": final_url, "detection_reason": "No title element was found in the fetched HTML.", "confidence": "HIGH", "observed_at": observed_at})
    if not meta: evidence.append({"status": "NOT_DETECTED", "fact": "Meta description not detected on the reviewed page", "snippet": "<head>", "source": final_url, "detection_reason": "No meta description element was found in the fetched HTML.", "confidence": "HIGH", "observed_at": observed_at})

    # Feature claims require a functional link or explicit action language. Mentions in
    # informational copy are deliberately insufficient evidence for a confirmed feature.
    links = re.findall(r"(?is)<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html)
    hrefs = [urljoin(final_url, href) for href, _ in links]
    link_text = [(urljoin(final_url, href), re.sub(r"(?is)<[^>]+>", " ", unescape(label)).strip()) for href, label in links]
    visible_text = re.sub(r"\s+", " ", unescape(re.sub(r"(?is)<[^>]+>", " ", text))).strip()
    script_urls = re.findall(r"(?is)<script\b[^>]*src=[\"']([^\"']+)[\"']", html)
    form_actions = re.findall(r"(?is)<form\b[^>]*action=[\"']([^\"']+)[\"']", html)

    booking_link = next(((url, label) for url, label in link_text if re.search(r"/(booking|appointments?|reserve|schedule)(?:[/#?]|$)", urlparse(url).path + ("?" if "?" in url else ""), re.I)), None)
    booking_cta = re.search(r"(?i)\b(?:book online|book an appointment|schedule an appointment)\b|записаться онлайн|запишитесь онлайн|записаться на при[её]м", visible_text)
    booking = booking_link or (("", booking_cta.group(0)) if booking_cta else None)

    whatsapp_link = next((url for url in hrefs if (urlparse(url).hostname or "").lower() in {"wa.me", "api.whatsapp.com"} and (urlparse(url).hostname == "wa.me" or urlparse(url).path.lower().startswith("/send"))), None)
    payment_urls = hrefs + [urljoin(final_url, item) for item in form_actions]
    payment_link = next((url for url in payment_urls if re.search(r"(?:paybox\.money|kaspi\.kz/(?:pay|shop)|/(?:checkout|payment|pay)(?:/|$|\?))", url, re.I)), None)
    crm_url = next((url for url in hrefs + [urljoin(final_url, item) for item in script_urls] if re.search(r"(?:amocrm|bitrix24|hubspot|salesforce|retailcrm|/crm(?:/|$|\?))", url, re.I)), None)

    signal_matches = {
        "mobile_friendly": (bool(re.search(r"name=[\"']viewport[\"']", html, re.I)), "viewport meta tag found"),
        "booking": (bool(booking), f"Booking action link or CTA: {booking[0] or booking[1]}" if booking else "No booking action link or explicit booking CTA found."),
        "whatsapp": (bool(whatsapp_link), f"WhatsApp action URL: {whatsapp_link}" if whatsapp_link else "No WhatsApp action URL found; a text mention alone is not a link."),
        "online_payment": (bool(payment_link), f"Payment or checkout endpoint: {payment_link}" if payment_link else "No payment or checkout endpoint found."),
        "ai_assistant": (bool(re.search(r"(chatbot|ai assistant|виртуальн\w+ помощник|чат-бот)", visible_text, re.I)), "AI assistant language detected in page text."),
        "crm": (bool(crm_url), f"CRM integration URL: {crm_url}" if crm_url else "No CRM integration URL found; informational mentions do not confirm CRM use."),
        "automation": (bool(re.search(r"(automated follow.?up|автоматическ\w+ рассыл|автоматизац\w+)", visible_text, re.I)), "Automation language detected in page text."),
        "forms": (bool(re.search(r"<form\b", html, re.I)), "HTML form element found"),
        "chat": (bool(re.search(r"(tawk|jivo|chatra|intercom|chat-widget)", html, re.I)), "Known chat widget marker found"),
    }
    signals = {}
    for key, (detected, snippet) in signal_matches.items():
        signals[key] = detected
        evidence.append({"status": "CONFIRMED" if detected else "NOT_DETECTED", "fact": f"{key} {'detected' if detected else 'not detected'} on the reviewed page", "snippet": snippet, "detection_reason": f"Deterministic page scan for {key}; a textual mention alone is not treated as functionality.", "source": final_url, "confidence": "HIGH" if detected else "LOW", "observed_at": observed_at})
    missing_quality = int(not title) + int(not meta) + int(not signals["mobile_friendly"])
    status = "WEBSITE_WEAK" if missing_quality >= 2 else "WEBSITE_OK"
    return {"website_status": status, "status": "CONFIRMED", "url": final_url, "http_status": http_status, "https": final_url.startswith("https://"), "response_time": response_time, "title": title, "meta_description": unescape(meta.group(1)).strip() if meta else "", "internal_links": len(internal), "page_size": len(html.encode("utf-8")), "signals": signals, "evidence": evidence, "source": final_url}
