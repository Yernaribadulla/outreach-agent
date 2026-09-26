from __future__ import annotations

import re
import time
import traceback
import uuid
from typing import Any

from .analysis.lm_studio import LMStudioClient, LMStudioError
from .discovery.engine import discover
from .discovery.providers import OpenStreetMapProvider
from .email.sender import SendError
from .generation.email_draft import generate_draft
from .storage.db import Database

MODEL = "qwen/qwen3-vl-8b"


def validate_draft(db: Database, lead: dict[str, Any], draft: dict[str, Any]) -> tuple[bool, str]:
    email = lead.get("email")
    if not email or lead.get("email_claim_status") != "CONFIRMED": return False, "confirmed business email required"
    if db.is_suppressed(email): return False, "recipient is suppressed"
    if db.conn.execute("SELECT 1 FROM send_logs WHERE lower(recipient)=lower(?)", (email,)).fetchone(): return False, "recipient already has send history"
    body = str(draft.get("body", ""))
    if not body.strip() or "traceback" in body.lower() or not lead.get("evidence"): return False, "missing evidence or invalid body"
    return True, "validated"


def run_autonomous(vertical: str, city: str, target: int, db_path: str, dry_run: bool = True, send: bool = False) -> dict[str, Any]:
    if send and not dry_run: raise SendError("Real send is intentionally disabled in development")
    db = Database(db_path)
    run_id = f"auto-{uuid.uuid4().hex[:12]}"
    run = discover([OpenStreetMapProvider()], vertical, f"стоматология {city}", target)
    stats = {"run_id": run_id, "discovered": len(run.candidates), "researched": 0, "qualified": 0, "emails_generated": 0, "emails_validated": 0, "ready_to_send": 0, "real_email_sends": 0, "errors": 0}
    errors: list[dict[str, Any]] = []
    db.create_autonomous_run(run_id, vertical, target, len(run.candidates))
    clinic_ids: dict[int, int] = {}
    for lead in run.candidates:
        clinic_ids[id(lead)] = db.add_clinic({"name": lead.get("name"), "website": lead.get("website"), "city": lead.get("city"), "phone": lead.get("phone"), "category": "dental", "source_url": lead.get("source_url"), "description": lead.get("description"), "profile": {**lead, "status": "DISCOVERED"}})
    client = LMStudioClient("http://127.0.0.1:1234/v1", MODEL)
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
            db.update_clinic_status(clinic_id, "RESEARCHING", lead); db.update_autonomous_run(run_id, researched=stats["researched"])
            for contact in lead.get("contacts", []):
                if contact.get("email"):
                    db.add_contact(clinic_id, contact["email"], contact.get("source_url", lead.get("website") or lead.get("source_url") or ""))
            stats["researched"] += 1
            db.update_autonomous_run(run_id, researched=stats["researched"])
            payload = {"company": {k: lead.get(k) for k in ("name", "city", "address", "website")}, "sources": lead.get("sources", []), "contacts": lead.get("contacts", []), "website_audit": lead.get("website_audit", {}), "evidence": lead.get("evidence", [])}
            stage = "AI_ANALYSIS"
            analysis = client.chat_opportunity(payload); db.add_analysis(clinic_id, MODEL, payload, analysis); stats["qualified"] += 1; db.update_clinic_status(clinic_id, "QUALIFIED", {**lead, "analysis": analysis}); db.update_autonomous_run(run_id, qualified=stats["qualified"])
            email = next((c.get("email") for c in lead.get("contacts", []) if c.get("email")), None)
            lead["email"] = email; lead["email_claim_status"] = "CONFIRMED" if email else "UNKNOWN"
            if not email: continue
            stage = "DRAFT_GENERATION"
            draft = generate_draft(client, lead, analysis, {"sender_name": "Ернар", "sender_brand": "DENTARA", "sender_contact": ""}); stats["emails_generated"] += 1
            stage = "VALIDATION"
            valid, reason = validate_draft(db, lead, draft)
            lead["validation"] = {"valid": valid, "reason": reason}
            if valid: stats["emails_validated"] += 1; stats["ready_to_send"] += 1; db.update_clinic_status(clinic_id, "READY_TO_SEND", {**lead, "analysis": analysis, "draft_validation": lead["validation"]})
        except Exception as exc:
            stats["errors"] += 1; lead["status"] = "ERROR"; lead["error"] = str(exc)[:200]
            detail = {"company": lead.get("name"), "stage": stage, "exception_type": type(exc).__name__, "message": str(exc)[:300], "traceback": traceback.format_exc(), "elapsed": round(time.perf_counter()-started, 3)}
            errors.append(detail); print(f"[AUTONOMOUS] company={lead.get('name')} stage={stage} error={type(exc).__name__}: {str(exc)[:240]}", flush=True); db.update_clinic_status(clinic_id, "ERROR", {**lead, "error_detail": detail})
    db.update_autonomous_run(run_id, status="DRY_RUN" if dry_run else "COMPLETE", researched=stats["researched"], qualified=stats["qualified"]); db.close(); return {**stats, "errors_detail": errors, "status": "DRY_RUN" if dry_run else "COMPLETE", "provider": "openstreetmap"}
