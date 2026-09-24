from __future__ import annotations

import re
from difflib import SequenceMatcher
from urllib.parse import urlparse


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-zа-я0-9]", "", (value or "").lower())


def _domain(url: str | None) -> str:
    return urlparse(url or "").netloc.lower().removeprefix("www.")


def match_confidence(left: dict, right: dict) -> float:
    if left.get("city") and right.get("city") and _norm(left["city"]) != _norm(right["city"]): return 0.0
    if left.get("phone") and right.get("phone") and _norm(left["phone"]) != _norm(right["phone"]): return 0.0
    if left.get("website") and right.get("website") and _domain(left["website"]) != _domain(right["website"]): return 0.0
    evidence = []
    if left.get("phone") and right.get("phone") and _norm(left["phone"]) == _norm(right["phone"]): evidence.append(0.45)
    if left.get("address") and right.get("address") and _norm(left["address"]) == _norm(right["address"]): evidence.append(0.30)
    if left.get("website") and right.get("website") and _domain(left["website"]) == _domain(right["website"]): evidence.append(0.35)
    if left.get("email") and right.get("email") and left["email"].lower() == right["email"].lower(): evidence.append(0.25)
    names = SequenceMatcher(None, _norm(left.get("name")), _norm(right.get("name"))).ratio()
    if names >= .92: evidence.append(.25)
    if not evidence or (names < .72 and len(evidence) < 2): return 0.0
    return round(min(0.99, sum(evidence)), 2)


def resolve_entities(records: list[dict]) -> list[dict]:
    entities: list[dict] = []
    for record in records:
        best = None; confidence = 0.0
        for entity in entities:
            score = match_confidence(record, entity)
            if score > confidence: best, confidence = entity, score
        if best is None or confidence < 0.70:
            entity = dict(record); entity["aliases"] = [record.get("name")] if record.get("name") else []
            entity["sources"] = list(record.get("sources", [])); entity["same_business_confidence"] = 1.0
            entities.append(entity); continue
        best["same_business_confidence"] = max(best.get("same_business_confidence", 0), confidence)
        if record.get("name") and record["name"] not in best.setdefault("aliases", []): best["aliases"].append(record["name"])
        best.setdefault("sources", []).extend(x for x in record.get("sources", []) if x not in best["sources"])
        for field in ("website", "address", "phone", "email"):
            if not best.get(field) and record.get(field): best[field] = record[field]
        best.setdefault("records", []).append(record)
    return entities


def contact_conflicts(records: list[dict]) -> list[dict]:
    conflicts = []
    for field in ("phone", "email", "website"):
        values = {(r.get(field) or "").strip() for r in records if r.get(field)}
        if len(values) > 1: conflicts.append({"type": "CONTACT_CONFLICT", "field": field, "values": sorted(values), "records": [r.get("source_url") for r in records]})
    return conflicts
