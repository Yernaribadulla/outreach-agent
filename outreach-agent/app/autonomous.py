from __future__ import annotations

import re
import os
import time
import traceback
import uuid
from typing import Any
from urllib.parse import urlparse

from .analysis.lm_studio import LMStudioClient, LMStudioError
from .analysis.context import build_llm_context, deduplicate_evidence
from .discovery.engine import discover
from .discovery.providers import OpenStreetMapProvider
from .email.sender import SendError
from .generation.email_draft import generate_draft, prepare_draft_formats
from .storage.db import Database
from .qualification import qualify_lead
from .verticals import get_vertical, build_search_query, sender_config

MODEL = "qwen/qwen3-vl-8b"


class EvidenceValidationError(ValueError):
    pass


CLAIM_TERMS = {
    "website": r"website|site|http",
    "mobile": r"mobile|viewport|responsive",
    "online_booking": r"booking|appointment|запис",
    "whatsapp": r"whatsapp|wa\.me",
    "online_payment": r"payment|оплат|kaspi|paybox",
    "ai_assistant": r"ai[- ](?:powered[- ])?assistant|ai[- ]powered chatbot|artificial intelligence|ии[- ]?ассистент|искусственн\w+ интеллект",
    "chat_widget": r"chat|чат|widget|виджет|tawk|jivo|chatra|intercom|livechat",
    "crm": r"\bcrm\b",
    "automation": r"automation|automated|автоматизац|follow.?up",
}


def prepare_evidence(payload: dict[str, Any]) -> list[dict[str, Any]]:
    company = payload.get("company") or {}
    records = deduplicate_evidence(payload.get("evidence") or [])
    used_ids = set()
    for index, item in enumerate(records, 1):
        evidence_id = str(item.get("evidence_id") or f"ev-{index:03d}")
        if evidence_id in used_ids:
            evidence_id = f"ev-{index:03d}"
        while evidence_id in used_ids:
            index += 1
            evidence_id = f"ev-{index:03d}"
        item["evidence_id"] = evidence_id
        used_ids.add(evidence_id)
        item["source"] = item.get("source") or item.get("source_url")
        explicit_owner = item.get("company_name") or item.get("company")
        if explicit_owner and str(explicit_owner).strip().casefold() != str(company.get("name") or "").strip().casefold():
            raise EvidenceValidationError(f"Evidence {item['evidence_id']} belongs to another company")
    payload["evidence"] = records
    return records


def validate_analysis(payload: dict[str, Any], analysis: dict[str, Any], allowed_evidence_ids: set[str] | None = None) -> dict[str, Any]:
    """Bind model claims to source-backed evidence and prevent unsupported confirmations."""
    if not isinstance(analysis, dict):
        raise EvidenceValidationError("LM response must be a JSON object")
    evidence = prepare_evidence(payload)
    company = payload.get("company") or {}
    official_host = urlparse(str(company.get("website") or "")).netloc.lower().removeprefix("www.")
    indexed = {item["evidence_id"]: item for item in evidence}
    state = analysis.setdefault("digital_state", {})
    if not isinstance(state, dict):
        raise EvidenceValidationError("digital_state must be an object")
    audit = payload.get("website_audit") or {}
    audit_status = audit.get("website_status", "UNKNOWN")
    if not company.get("website") or audit_status in {"UNKNOWN", "NO_WEBSITE_FOUND", "WEBSITE_UNAVAILABLE"}:
        state["website"] = {"status": "UNKNOWN", "reason": "No confirmed official website audit evidence was supplied.", "evidence_ids": [], "confidence": "LOW"}
        analysis["why_this_lead"] = ["No confirmed official website was found in the supplied evidence; this remains UNKNOWN and is not proof that no website exists."]
        analysis["recommended_angle"] = "Verify the clinic's official digital presence before selecting an outreach angle."
        analysis["sales_brief"] = "The supplied evidence does not confirm an official website or a digital opportunity. Further verification is required."
        priority = analysis.setdefault("priority", {})
        for key in ("score", "website_opportunity", "booking_opportunity", "crm_opportunity", "ai_opportunity", "automation_opportunity"):
            priority[key] = 0 if key == "score" else False
    signals = audit.get("signals") or {}
    allowed = {"mobile": signals.get("mobile_friendly"), "online_booking": signals.get("booking"), "whatsapp": signals.get("whatsapp"), "online_payment": signals.get("online_payment"), "ai_assistant": signals.get("ai_assistant")}
    for key, detected in allowed.items():
        if key in state and isinstance(state[key], dict) and detected is False and state[key].get("status") == "CONFIRMED":
            state[key] = {"status": "NOT_DETECTED", "reason": "The deterministic audit did not detect this signal; absence is not proven.", "evidence_ids": [], "confidence": "LOW"}
    chat_widget_detected = signals.get("chat_widget") is True or signals.get("chat") is True
    if chat_widget_detected:
        chat_evidence_ids = [
            str(item.get("evidence_id")) for item in evidence
            if item.get("status") == "CONFIRMED" and re.search(r"chat_widget|chat widget|chat detected|чат-виджет|widget marker", " ".join(str(item.get(field) or "") for field in ("fact", "snippet", "detection_reason")), re.I)
            and item.get("evidence_id")
        ]
        state["chat_widget"] = {
            "status": "CONFIRMED", "reason": "The website audit found a chat widget marker; this does not establish AI capability.",
            "evidence_ids": chat_evidence_ids, "confidence": "HIGH" if chat_evidence_ids else "LOW",
        }
        ai_claim = state.get("ai_assistant")
        if not isinstance(ai_claim, dict) or str(ai_claim.get("status", "UNKNOWN")).upper() == "NOT_DETECTED":
            state["ai_assistant"] = {
                "status": "UNKNOWN", "reason": "A chat widget is confirmed, but the available evidence does not show whether it is AI-powered.",
                "evidence_ids": chat_evidence_ids, "confidence": "LOW",
            }
    allowed_statuses = {"CONFIRMED", "INFERRED", "UNKNOWN", "NOT_DETECTED"}
    for key, claim in list(state.items()):
        if not isinstance(claim, dict):
            state[key] = {"status": "UNKNOWN", "reason": "Malformed claim; manual review is required.", "evidence_ids": [], "confidence": "LOW"}
            continue
        status = str(claim.get("status", "UNKNOWN")).upper()
        if status not in allowed_statuses:
            claim["status"] = "UNKNOWN"
            claim["reason"] = "Unsupported claim status; manual review is required."
            claim["evidence_ids"] = []
            claim["confidence"] = "LOW"
            continue
        claim["status"] = status
        refs = claim.get("evidence_ids") or []
        if not isinstance(refs, list):
            raise EvidenceValidationError(f"Evidence IDs for {key} must be a list")
        unknown_ids = [item for item in refs if item not in indexed or (allowed_evidence_ids is not None and item not in allowed_evidence_ids)]
        if unknown_ids:
            raise EvidenceValidationError(f"Unknown evidence ID for {key}: {unknown_ids[0]}")
        usable_refs = []
        invalid_source = False
        for evidence_id in refs:
            item = indexed[evidence_id]
            source = str(item.get("source") or "")
            parsed = urlparse(source)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                invalid_source = True
                continue
            source_host = parsed.netloc.lower().removeprefix("www.")
            owner = str(item.get("company_name") or item.get("company") or "").strip().casefold()
            owner_matches = not owner or owner == str(company.get("name") or "").strip().casefold()
            if not owner_matches:
                raise EvidenceValidationError(f"Evidence {evidence_id} belongs to another company")
            if not owner and (not official_host or source_host != official_host):
                invalid_source = True
                continue
            description = " ".join(str(item.get(field) or "") for field in ("fact", "snippet", "detection_reason"))
            if status in {"CONFIRMED", "INFERRED"} and (item.get("status") != "CONFIRMED" or not re.search(CLAIM_TERMS.get(key, re.escape(key)), description, re.I)):
                invalid_source = True
                continue
            usable_refs.append(evidence_id)
        claim["evidence_ids"] = usable_refs
        if invalid_source or (status == "CONFIRMED" and not usable_refs):
            claim["status"] = "UNKNOWN"
            claim["reason"] = "Supporting evidence is missing or its source/company ownership is unverified."
            claim["evidence_ids"] = []
            claim["confidence"] = "LOW"
    return analysis


def validate_draft(db: Database, lead: dict[str, Any], draft: dict[str, Any]) -> tuple[bool, str]:
    email = lead.get("email")
    if not email or lead.get("email_claim_status") != "CONFIRMED": return False, "confirmed business email required"
    if db.is_suppressed(email): return False, "recipient is suppressed"
    if db.conn.execute("SELECT 1 FROM send_logs WHERE lower(recipient)=lower(?)", (email,)).fetchone(): return False, "recipient already has send history"
    body = str(draft.get("body", ""))
    if not body.strip() or "traceback" in body.lower() or not lead.get("evidence"): return False, "missing evidence or invalid body"
    if not draft.get("rationale") or not draft.get("source_observations"): return False, "draft rationale and source observations are required"
    return True, "validated"


def run_autonomous(vertical: str, city: str, target: int, db_path: str, dry_run: bool = True, send: bool = False) -> dict[str, Any]:
    if send and not dry_run: raise SendError("Real send is intentionally disabled in development")
    db = Database(db_path)
    run_id = f"auto-{uuid.uuid4().hex[:12]}"
    vertical_config = get_vertical(vertical)
    run = discover([OpenStreetMapProvider(vertical_config)], vertical, build_search_query(vertical_config, city), target)
    stats = {"run_id": run_id, "discovered": len(run.candidates), "researched": 0, "qualified": 0, "needs_review": 0, "disqualified": 0, "emails_generated": 0, "emails_validated": 0, "ready_to_review": 0, "real_email_sends": 0, "errors": 0}
    errors: list[dict[str, Any]] = []
    db.create_autonomous_run(run_id, vertical, target, len(run.candidates))
    clinic_ids: dict[int, int] = {}
    for lead in run.candidates:
        lead["category"] = vertical_config.category
        clinic_ids[id(lead)] = db.add_clinic({"name": lead.get("name"), "website": lead.get("website"), "city": lead.get("city"), "phone": lead.get("phone"), "category": vertical_config.category, "source_url": lead.get("source_url"), "description": lead.get("description"), "profile": {**lead, "status": "DISCOVERED"}})
    client = LMStudioClient(os.environ.get("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1"), MODEL)
    try:
        available = {item.get("id") for item in client.health().get("data", [])}
        if MODEL not in available: raise LMStudioError(f"required model unavailable: {MODEL}")
    except Exception as exc:
        detail = {"stage": "AI_ANALYSIS", "exception_type": type(exc).__name__, "message": str(exc)[:300], "traceback": traceback.format_exc(), "elapsed": 0.0}
        errors.append(detail); db.update_autonomous_run(run_id, status="AI_UNAVAILABLE", discovered=len(run.candidates)); db.close(); return {**stats, "status": "AI_UNAVAILABLE", "errors_detail": errors, "error": detail["message"]}
    for lead in run.candidates:
        started = time.perf_counter(); clinic_id = clinic_ids[id(lead)]; stage = "PERSIST"
        try:
            lead["status"] = "RESEARCHING"
            stage = "RESEARCH"
            db.update_clinic_status(clinic_id, "RESEARCHED", lead); db.update_autonomous_run(run_id, researched=stats["researched"])
            contact_ids = {}
            for contact in lead.get("contacts", []):
                if contact.get("email"):
                    contact_id = db.add_contact(clinic_id, contact["email"], contact.get("source_url") or contact.get("source") or lead.get("website") or lead.get("source_url") or "")
                    if contact_id: contact_ids[contact["email"].lower()] = contact_id
            stats["researched"] += 1
            db.update_autonomous_run(run_id, researched=stats["researched"])
            payload = {"company": {k: lead.get(k) for k in ("name", "city", "address", "website")}, "sources": lead.get("sources", []), "contacts": lead.get("contacts", []), "website_resolution": lead.get("website_resolution", {}), "website_audit": lead.get("website_audit", {}), "evidence": lead.get("evidence", [])}
            stage = "AI_ANALYSIS"
            prepare_evidence(payload)
            model_context = build_llm_context(payload, vertical_config.opportunity_signals)
            model_evidence_ids = {str(item["evidence_id"]) for item in model_context["key_evidence"]}
            analysis = validate_analysis(payload, client.chat_opportunity(model_context), model_evidence_ids); db.add_analysis(clinic_id, MODEL, payload, analysis)
            lead["evidence"] = payload["evidence"]
            qualification = qualify_lead(lead, analysis, vertical_config)
            lead["qualification"] = qualification
            db.update_clinic_status(clinic_id, qualification["status"], {**lead, "analysis": analysis})
            stats[qualification["status"].lower()] += 1
            db.update_autonomous_run(run_id, qualified=stats["qualified"])
            if qualification["status"] != "QUALIFIED": continue
            existing_draft = db.active_draft(clinic_id)
            if existing_draft:
                db.update_clinic_status(clinic_id, "APPROVED" if existing_draft["status"] == "APPROVED" else "DRAFTED", {**lead, "analysis": analysis, "draft_id": existing_draft["id"]})
                stats["ready_to_review"] += 1
                continue
            email = next((c.get("email") for c in lead.get("contacts", []) if c.get("email") and c.get("source_url") or c.get("email") and c.get("source")), None)
            lead["email"] = email; lead["email_claim_status"] = "CONFIRMED" if email else "UNKNOWN"
            contact_id = contact_ids.get(str(email).lower()) if email else None
            if not email or not contact_id: continue
            stage = "DRAFT_GENERATION"
            draft = generate_draft(client, lead, analysis, sender_config(vertical_config, os.environ)); stats["emails_generated"] += 1
            draft = prepare_draft_formats(draft)
            stage = "VALIDATION"
            valid, reason = validate_draft(db, lead, draft)
            lead["validation"] = {"valid": valid, "reason": reason}
            if valid:
                stats["emails_validated"] += 1
                draft_id = db.add_draft(clinic_id, contact_id, draft)
                stats["ready_to_review"] += 1
                db.update_clinic_status(clinic_id, "DRAFTED", {**lead, "analysis": analysis, "draft_validation": lead["validation"], "draft_id": draft_id})
        except Exception as exc:
            stats["errors"] += 1; lead["status"] = "ERROR"; lead["error"] = str(exc)[:200]
            detail = {"company": lead.get("name"), "stage": stage, "exception_type": type(exc).__name__, "message": str(exc)[:300], "traceback": traceback.format_exc(), "elapsed": round(time.perf_counter()-started, 3)}
            errors.append(detail); print(f"[AUTONOMOUS] company={lead.get('name')} stage={stage} error={type(exc).__name__}: {str(exc)[:240]}", flush=True); db.update_clinic_status(clinic_id, "ERROR", {**lead, "error_detail": detail})
    db.update_autonomous_run(run_id, status="DRY_RUN" if dry_run else "COMPLETE", researched=stats["researched"], qualified=stats["qualified"]); db.close(); return {**stats, "errors_detail": errors, "status": "DRY_RUN" if dry_run else "COMPLETE", "provider": "openstreetmap"}
