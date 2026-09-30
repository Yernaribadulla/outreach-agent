from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from typing import Any


LLM_CONTEXT_MAX_CHARS = 8_000
LLM_CONTEXT_MAX_EVIDENCE = 4
LLM_CONTEXT_MAX_CONTACTS = 3
LLM_CONTEXT_MAX_SOURCES = 4

_SIGNAL_ALIASES = {
    "mobile": ("mobile_friendly", "mobile"),
    "online_booking": ("online_booking", "booking"),
    "booking": ("booking", "online_booking"),
    "whatsapp": ("whatsapp",),
    "online_payment": ("online_payment", "payment"),
    "forms": ("forms", "contact_form"),
    "chat_widget": ("chat_widget", "chat"),
    "ai_assistant": ("ai_assistant",),
    "crm": ("crm",),
    "automation": ("automation",),
}
_EVIDENCE_TERMS = {
    "online_booking": r"booking|appointment|запис",
    "booking": r"booking|appointment|запис",
    "ai_assistant": r"ai|assistant|искусственн|ии",
    "crm": r"\bcrm\b",
    "online_payment": r"payment|оплат|kaspi|paybox",
    "whatsapp": r"whatsapp|wa\.me",
    "automation": r"automation|automated|автоматизац|follow.?up",
    "chat_widget": r"chat|чат|widget|виджет|tawk|jivo|chatra|intercom|livechat",
    "forms": r"form|форма|заявк",
    "mobile": r"mobile|viewport|responsive|мобильн",
    "website": r"website|site|http|сайт",
}
_STATUS_RANK = {"CONFIRMED": 0, "INFERRED": 1, "UNKNOWN": 2, "NOT_DETECTED": 3}


def _normal_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]*>", " ", text)
    return " ".join(text.split()).casefold()


def normalize_source_url(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        if not parts.scheme or not parts.netloc:
            return raw.rstrip("/").casefold()
        scheme = parts.scheme.lower()
        host = parts.netloc.lower()
        path = parts.path.rstrip("/") or "/"
        query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
        return urlunsplit((scheme, host, path, query, ""))
    except ValueError:
        return raw.rstrip("/").casefold()


def evidence_dedup_key(item: dict[str, Any]) -> tuple[str, str, str, str]:
    claim = _normal_text(item.get("claim") or item.get("type") or item.get("fact") or item.get("label"))
    source = normalize_source_url(item.get("source") or item.get("source_url"))
    text = _normal_text(item.get("snippet") or item.get("text") or item.get("fact") or item.get("detection_reason"))
    status = str(item.get("status") or "UNKNOWN").strip().upper()
    return claim, source, text, status


def deduplicate_evidence(records: Iterable[object] | None) -> list[dict[str, Any]]:
    """Collapse repeated observations while preserving separate source provenance."""
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in records or []:
        item = dict(raw) if isinstance(raw, dict) else {"fact": str(raw)}
        item.setdefault("source", item.get("source_url"))
        key = evidence_dedup_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _signal_status(signals: dict[str, Any], name: str) -> str:
    for alias in _SIGNAL_ALIASES.get(name, (name,)):
        if alias not in signals:
            continue
        value = signals[alias]
        if isinstance(value, dict):
            value = value.get("status")
        if value is True or str(value).upper() == "CONFIRMED":
            return "CONFIRMED"
        if value is False or str(value).upper() == "NOT_DETECTED":
            return "NOT_DETECTED"
    return "UNKNOWN"


def _claim_category(item: dict[str, Any]) -> str:
    haystack = " ".join(str(item.get(k) or "") for k in ("claim", "type", "fact", "snippet", "text", "detection_reason")).casefold()
    for category, pattern in _EVIDENCE_TERMS.items():
        if re.search(pattern, haystack, re.I):
            return category
    return "other"


def _compact_evidence(records: Iterable[object] | None, opportunity_signals: Iterable[str]) -> list[dict[str, Any]]:
    evidence = deduplicate_evidence(records)
    configured = set(opportunity_signals)
    ranked = []
    used_ids: set[str] = set()
    for index, item in enumerate(evidence, 1):
        evidence_id = str(item.get("evidence_id") or f"ev-{index:03d}")
        if evidence_id in used_ids:
            evidence_id = f"ev-{index:03d}"
        used_ids.add(evidence_id)
        status = str(item.get("status") or "UNKNOWN").upper()
        category = _claim_category(item)
        opportunity_rank = 0 if status == "CONFIRMED" and category in configured else 1
        ranked.append((opportunity_rank, _STATUS_RANK.get(status, 4), index, evidence_id, item, category))
    ranked.sort(key=lambda row: (row[0], row[1], row[2]))
    result = []
    for _, _, _, evidence_id, item, category in ranked[:LLM_CONTEXT_MAX_EVIDENCE]:
        compact = {
            "evidence_id": evidence_id,
            "category": category,
            "status": str(item.get("status") or "UNKNOWN").upper(),
            "claim": str(item.get("claim") or item.get("type") or item.get("fact") or "").strip()[:120],
            "text": str(item.get("snippet") or item.get("text") or item.get("fact") or item.get("detection_reason") or "").strip()[:260],
            "source": str(item.get("source") or item.get("source_url") or "").strip()[:320],
            "confidence": str(item.get("confidence") or "UNKNOWN")[:20],
        }
        if item.get("detection_reason"):
            compact["reason"] = str(item["detection_reason"]).strip()[:160]
        result.append(compact)
    return result


def _unique_urls(values: Iterable[object]) -> list[str]:
    out, seen = [], set()
    for value in values:
        if isinstance(value, dict):
            value = value.get("url") or value.get("source_url") or value.get("source")
        url = str(value or "").strip()
        key = normalize_source_url(url)
        try:
            parsed = urlsplit(url) if url else None
        except ValueError:
            parsed = None
        if parsed and parsed.scheme in {"http", "https"} and parsed.netloc and key and key not in seen:
            seen.add(key)
            out.append(url[:320])
    return out


def build_llm_context(
    payload: dict[str, Any],
    opportunity_signals: Iterable[str] = (),
    *,
    max_chars: int = LLM_CONTEXT_MAX_CHARS,
) -> dict[str, Any]:
    """Build a deterministic, compact model-only view; caller retains full research evidence."""
    company_raw = payload.get("company") or payload.get("clinic") or {}
    audit = payload.get("website_audit") or {}
    signals = audit.get("signals") or {}
    configured = tuple(opportunity_signals or payload.get("vertical_opportunity_signals") or ())
    if "website_summary" in payload and not audit:
        website_summary = dict(payload.get("website_summary") or {})
        opportunity = list(payload.get("opportunity_signals") or [])
    else:
        website_summary = {
            "status": str(audit.get("website_status") or audit.get("status") or "UNKNOWN")[:40],
            **{key: _signal_status(signals, key) for key in (
                "mobile", "online_booking", "whatsapp", "online_payment", "forms",
                "chat_widget", "ai_assistant", "crm", "automation",
            )},
        }
        opportunity = [signal for signal in configured if _signal_status(signals, signal) == "CONFIRMED"]

    compact_contacts = []
    contacts_seen = set()
    for contact in payload.get("contacts") or []:
        if not isinstance(contact, dict):
            continue
        email = str(contact.get("email") or "").strip().lower()
        phone = str(contact.get("phone") or contact.get("value") or "").strip()
        source = str(contact.get("source") or contact.get("source_url") or "").strip()
        if not (email or phone) or not source:
            continue
        key = (email, phone, normalize_source_url(source))
        if key in contacts_seen:
            continue
        contacts_seen.add(key)
        item = {"type": str(contact.get("type") or contact.get("kind") or "PUBLIC_BUSINESS_CONTACT")[:40], "source": source[:320]}
        if email:
            item["email"] = email[:180]
        if phone:
            item["phone"] = phone[:80]
        compact_contacts.append(item)
        if len(compact_contacts) == LLM_CONTEXT_MAX_CONTACTS:
            break

    key_evidence = _compact_evidence(payload.get("key_evidence") or payload.get("evidence"), configured)
    evidence_urls = [item.get("source") for item in key_evidence]
    contact_urls = [item.get("source") for item in compact_contacts]
    raw_sources = payload.get("sources") or []
    if isinstance(raw_sources, (str, dict)):
        raw_sources = [raw_sources]
    source_candidates = [company_raw.get("website"), audit.get("url") or audit.get("source"), *evidence_urls, *contact_urls, *raw_sources]
    sources = _unique_urls(source_candidates)[:LLM_CONTEXT_MAX_SOURCES]

    context = {
        "company": {key: str(company_raw.get(key) or "")[:limit] for key, limit in (("name", 180), ("city", 100), ("address", 180), ("website", 320)) if company_raw.get(key)},
        "website_summary": website_summary,
        "contacts": compact_contacts,
        "key_evidence": key_evidence,
        "opportunity_signals": opportunity,
        "sources": sources,
    }
    while len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) > max_chars:
        if context["sources"]:
            context["sources"].pop()
        elif context["key_evidence"]:
            context["key_evidence"].pop()
        elif context["contacts"]:
            context["contacts"].pop()
        elif context["company"].get("address"):
            context["company"]["address"] = context["company"]["address"][: max(0, len(context["company"]["address"]) - 128)]
        elif context["company"].get("name"):
            context["company"]["name"] = context["company"]["name"][: max(0, len(context["company"]["name"]) - 32)]
        else:
            raise ValueError("LLM context cannot fit the configured character budget")
    return context
