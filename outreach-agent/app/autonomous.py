from __future__ import annotations

import re
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
    run = discover([OpenStreetMapProvider()], vertical, f"стоматология {city}", target)
    stats = {"discovered": len(run.candidates), "researched": 0, "qualified": 0, "emails_generated": 0, "emails_validated": 0, "ready_to_send": 0, "real_email_sends": 0, "errors": 0}
    client = LMStudioClient("http://127.0.0.1:1234/v1", MODEL)
    try: client.health()
    except Exception as exc:
        db.close(); return {**stats, "status": "LM_STUDIO_UNAVAILABLE", "error": str(exc)[:200]}
    for lead in run.candidates:
        try:
            clinic_id = db.add_clinic({"name": lead.get("name"), "website": lead.get("website"), "city": lead.get("city"), "phone": lead.get("phone"), "category": "dental", "source_url": lead.get("source_url"), "description": lead.get("description"), "profile": lead})
            lead["status"] = "RESEARCHING"
            for contact in lead.get("contacts", []):
                if contact.get("email"):
                    db.add_contact(clinic_id, contact["email"], contact.get("source_url", lead.get("website") or lead.get("source_url") or ""))
            stats["researched"] += 1
            payload = {"company": {k: lead.get(k) for k in ("name", "city", "address", "website")}, "sources": lead.get("sources", []), "contacts": lead.get("contacts", []), "website_audit": lead.get("website_audit", {}), "evidence": lead.get("evidence", [])}
            analysis = client.chat_opportunity(payload); db.add_analysis(clinic_id, MODEL, payload, analysis); stats["qualified"] += 1
            email = next((c.get("email") for c in lead.get("contacts", []) if c.get("email")), None)
            lead["email"] = email; lead["email_claim_status"] = "CONFIRMED" if email else "UNKNOWN"
            if not email: continue
            draft = generate_draft(client, lead, analysis, {"sender_name": "Ернар", "sender_brand": "DENTARA", "sender_contact": ""}); stats["emails_generated"] += 1
            valid, reason = validate_draft(db, lead, draft)
            lead["validation"] = {"valid": valid, "reason": reason}
            if valid: stats["emails_validated"] += 1; stats["ready_to_send"] += 1
        except Exception as exc:
            stats["errors"] += 1; lead["status"] = "ERROR"; lead["error"] = str(exc)[:200]
    db.close(); return {**stats, "status": "DRY_RUN" if dry_run else "COMPLETE", "provider": "openstreetmap"}
