from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .verticals import VerticalConfig, get_vertical


def qualify_lead(lead: dict[str, Any], analysis: dict[str, Any], vertical: str | VerticalConfig) -> dict[str, Any]:
    """Deterministic, explainable qualification. An AI response alone never qualifies a lead."""
    config = get_vertical(vertical) if isinstance(vertical, str) else vertical
    category = str(lead.get("category") or "").strip().lower()
    explicit_category = category not in {"", "unknown", "other"}
    icp_fit = category == config.category if explicit_category else None

    evidence = [item for item in lead.get("evidence", []) if isinstance(item, dict)]
    evidence_quality = any(
        item.get("status") == "CONFIRMED"
        and urlparse(str(item.get("source") or item.get("source_url") or "")).scheme in {"http", "https"}
        for item in evidence
    )
    state = analysis.get("digital_state") or {}
    opportunity_fit = any(
        isinstance(state.get(signal), dict)
        and state[signal].get("status") in {"CONFIRMED", "INFERRED"}
        and bool(state[signal].get("evidence_ids"))
        for signal in config.opportunity_signals
    )
    contacts = lead.get("contacts") or []
    contactability = any(
        isinstance(contact, dict)
        and bool(contact.get("email") or contact.get("phone") or contact.get("value"))
        and bool(contact.get("source") or contact.get("source_url"))
        for contact in contacts
    )
    try:
        confidence = float(analysis.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0

    factors = {
        "icp_fit": icp_fit,
        "evidence_quality": evidence_quality,
        "opportunity_fit": opportunity_fit,
        "contactability": contactability,
        "outreach_confidence": confidence,
    }
    reasons = []
    if icp_fit is False:
        reasons.append(f"Компания явно относится к категории {category}, а не {config.category}.")
        status = "DISQUALIFIED"
    else:
        if icp_fit is None: reasons.append("Вертикаль компании не подтверждена источником.")
        if not evidence_quality: reasons.append("Нет пригодного подтверждённого evidence с публичным источником.")
        if not opportunity_fit: reasons.append("Возможность для выбранного vertical не подтверждена AI evidence.")
        if not contactability: reasons.append("Не найден публичный контакт с provenance.")
        if confidence < 0.65: reasons.append("Уверенность outreach ниже порога 0.65.")
        status = "QUALIFIED" if icp_fit is True and evidence_quality and opportunity_fit and contactability and confidence >= 0.65 else "NEEDS_REVIEW"
        if status == "QUALIFIED": reasons.append("ICP, evidence, opportunity, публичный контакт и confidence прошли заданные правила.")
    return {"status": status, "factors": factors, "reasons": reasons, "rules_version": 1}
