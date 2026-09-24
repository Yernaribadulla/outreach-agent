from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .providers import DiscoveryProvider
from .resolution import contact_conflicts, resolve_entities
from ..website_audit import audit_website
from ..extraction.public_page import extract_business_contacts
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import time


def _official_candidate(entity: dict) -> str | None:
    urls = [entity.get("website"), entity.get("source_url")]
    urls.extend(x.get("source_url", "") for x in entity.get("sources", []) if isinstance(x, dict))
    blocked = ("2gis.", "yandex.", "google.", "bing.")
    for url in urls:
        host = urlparse(url or "").netloc.lower()
        if host and not any(part in host for part in blocked): return url
    return None


@dataclass
class DiscoveryRun:
    vertical: str
    query: str
    activity: list[dict[str, Any]] = field(default_factory=list)
    source_status: dict[str, str] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)


def discover(providers: list[DiscoveryProvider], vertical: str, query: str, target_count: int = 10, audit_websites: bool = True) -> DiscoveryRun:
    run = DiscoveryRun(vertical, query)
    records = []
    for provider in providers:
        started = time.perf_counter()
        print(f"[DISCOVERY] Starting {provider.name}", flush=True)
        run.activity.append({"type": "SEARCH", "provider": provider.name, "message": f"Searching {provider.name}..."})
        try:
            found = provider.search(query, target_count)
            for item in found:
                item = dict(item); item.setdefault("sources", []).append(provider.name); records.append(item)
            run.source_status[provider.name] = "SUCCESS"
            run.activity.append({"type": "RESULT", "provider": provider.name, "count": len(found)})
            print(f"[DISCOVERY] {provider.name} SUCCESS candidates={len(found)} elapsed={time.perf_counter()-started:.2f}s", flush=True)
        except Exception as exc:
            run.source_status[provider.name] = "SOURCE_UNAVAILABLE"
            run.activity.append({"type": "SOURCE_UNAVAILABLE", "provider": provider.name, "error": str(exc)[:180]})
            print(f"[DISCOVERY] {provider.name} SOURCE_UNAVAILABLE type={type(exc).__name__} elapsed={time.perf_counter()-started:.2f}s reason={str(exc)[:180]}", flush=True)
    run.activity.append({"type": "RESOLUTION", "message": "Resolving duplicates..."})
    entities = resolve_entities(records)
    for entity in entities:
        source_records = entity.get("records", [entity])
        entity["conflicts"] = contact_conflicts(source_records)
        if not entity.get("website"):
            candidate = _official_candidate(entity)
            if candidate and urlparse(candidate).scheme in {"http", "https"}:
                entity["website"] = candidate
                entity["website_resolution"] = {"status": "WEBSITE_CANDIDATE", "evidence": [candidate]}
            else:
                entity["website_resolution"] = {"status": "NO_OFFICIAL_WEBSITE_FOUND", "evidence": []}
        entity["website_audit"] = audit_website(entity.get("website"), timeout=8) if audit_websites else {"website_status": "UNKNOWN", "status": "UNKNOWN", "evidence": []}
        entity["evidence"] = entity["website_audit"].get("evidence", [])
        entity["website_status"] = entity["website_audit"].get("website_status", "UNKNOWN")
        entity["contacts"] = list(entity.get("contacts", []))
        if audit_websites and entity.get("website"):
            try:
                request = Request(entity["website"], headers={"User-Agent": "B2B-Lead-Research/1.0"})
                with urlopen(request, timeout=8) as response:
                    html = response.read(300_000).decode("utf-8", errors="ignore")
                entity["contacts"].extend(extract_business_contacts(html, response.geturl()))
            except Exception as exc:
                entity["evidence"].append({"status": "UNKNOWN", "fact": "Public contact page could not be fetched", "source": entity["website"], "confidence": "LOW", "error": str(exc)[:120]})
    run.candidates = entities[:target_count]
    run.activity.append({"type": "RESOLUTION_COMPLETE", "count": len(run.candidates), "duplicates_removed": max(0, len(records) - len(entities))})
    return run
