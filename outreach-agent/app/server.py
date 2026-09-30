from __future__ import annotations

import json
import os
import re
import random
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import smtplib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.analysis.lm_studio import LMStudioClient
from app.discovery.public_search import discover_candidates, mock_candidates
from app.discovery.datasets import curated_candidates
from app.discovery.engine import discover
from app.discovery.providers import DuckDuckGoProvider, WebProvider
from app.email.sender import SMTPProvider, SimulatedProvider
from app.extraction.public_page import inspect_site
from app.website_audit import audit_website
from app.generation.email_draft import generate_draft, prepare_draft_formats
from app.autonomous import validate_analysis, EvidenceValidationError
from app.storage.db import Database, utc_now
from app.qualification import qualify_lead
from app.verticals import get_vertical, build_search_query, sender_config, VERTICALS

DB = Database(os.environ.get("OUTREACH_DB_PATH", str(ROOT / "data" / "outreach.db")))
UI = ROOT / "app" / "ui" / "index.html"
DISCOVERY_RUNS: dict[str, dict] = {}
DISCOVERY_LOCK = threading.Lock()
ANALYSIS_LOCK = threading.Lock()
ANALYZING_LEADS: set[int] = set()
BATCH_LOCK = threading.Lock()
SEND_LOCK = threading.Lock()
SENDING_DRAFTS: set[int] = set()


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1); env.setdefault(key.strip(), value.strip())
    return env


def config(env: dict[str, str], vertical: str = "dental") -> dict[str, str]:
    return sender_config(vertical, env)


def discovery_limit(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("Discovery limit must be a positive integer.")
    if isinstance(value, int):
        limit = value
    elif isinstance(value, str) and re.fullmatch(r"[0-9]+", value.strip()):
        limit = int(value.strip())
    else:
        raise ValueError("Discovery limit must be a positive integer.")
    if not 1 <= limit <= 100:
        raise ValueError("Discovery limit must be between 1 and 100.")
    return limit


def lm_client(env: dict[str, str]) -> LMStudioClient:
    required = "qwen/qwen3-vl-8b"
    configured = env.get("LM_STUDIO_MODEL", required)
    if configured not in {required, ""}: raise RuntimeError(f"Требуется только модель {required}; настроена другая модель: {configured}")
    client = LMStudioClient(env.get("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1"), required)
    try: available = {item.get("id") for item in client.health().get("data", [])}
    except Exception as exc: raise RuntimeError(f"LM Studio недоступен: {exc}") from exc
    if required not in available: raise RuntimeError(f"Модель {required} недоступна в LM Studio; автоматическая замена запрещена")
    return client


def build_email_formats(draft: dict) -> dict:
    return prepare_draft_formats(draft)


def validate_dashboard_draft(draft: dict, contacts: list[dict], evidence: list[dict], sender: dict | None = None) -> tuple[bool, str]:
    subject, body = str(draft.get("subject", "")), str(draft.get("body", ""))
    if not subject.strip() or not body.strip(): return False, "subject and body are required"
    if not str(draft.get("rationale") or "").strip(): return False, "draft rationale is required"
    if not isinstance(draft.get("source_observations"), list) or not draft.get("source_observations"): return False, "source observations are required"
    demo_url = str((sender or {}).get("demo_url") or "")
    if demo_url and demo_url not in body: return False, "configured demo URL is missing"
    allowed_emails = {str(c.get("email", "")).lower() for c in contacts if c.get("email")}
    for email in re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", body):
        if email.lower() not in allowed_emails: return False, f"unsupported email in draft: {email}"
    urls = re.findall(r"https?://[^\s)<>]+", body)
    allowed_urls = {demo_url} if demo_url else set()
    allowed_urls.update(str(e.get("source") or e.get("source_url") or "").rstrip(".,") for e in evidence)
    if any(url.rstrip(".,") not in allowed_urls for url in urls):
        return False, "unsupported URL in draft"
    if not evidence: return False, "evidence is required"
    return True, "validated"


def run_pipeline(candidate: dict, use_mock: bool = False) -> dict:
    vertical = get_vertical(str(candidate.get("category") or "dental"))
    sender = config(load_env(), vertical.key)
    candidate["category"] = vertical.category
    clinic_id = DB.add_clinic(candidate)
    if use_mock:
        site = {"website": candidate["website"], "text": candidate.get("description", ""), "pages": [], "contacts": [{"email": "hello@orda-smile.example", "source_url": candidate["contact_page"], "kind": "generic_business"}], "errors": []}
    elif candidate.get("precollected"):
        site = {"website": candidate.get("website", ""), "text": json.dumps(candidate.get("profile", candidate), ensure_ascii=False), "pages": [], "contacts": [{"email": candidate["email"], "source_url": candidate["contact_source"], "kind": "PUBLIC_BUSINESS_EMAIL"}], "errors": []}
    else:
        site = inspect_site(candidate["website"])
    contacts = site["contacts"]
    if not contacts: return {"clinic_id": clinic_id, "status": "NO_PUBLIC_BUSINESS_EMAIL", "contacts": []}
    contact_id = DB.add_contact(clinic_id, contacts[0]["email"], contacts[0]["source_url"], contacts[0]["kind"])
    if not contact_id: return {"clinic_id": clinic_id, "status": "NO_CONTACT_RECORD", "contacts": contacts}
    if DB.is_suppressed(contacts[0]["email"]): return {"clinic_id": clinic_id, "status": "DO_NOT_CONTACT", "contacts": contacts}
    snapshot = {"clinic": candidate, "public_site": {"text": site["text"], "pages": [{"url": p["url"], "text": p["text"][:1800]} for p in site["pages"]], "contact_sources": contacts}}
    env = load_env()
    try: client = lm_client(env)
    except Exception as exc:
        print(f"[PIPELINE] LM Studio unavailable for clinic_id={clinic_id}: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        return {"clinic_id": clinic_id, "status": "ANALYSIS_FAILED", "error": "LM Studio is unavailable or the configured model is not loaded.", "contacts": contacts}
    try:
        if use_mock:
            analysis = {"clinic_summary": "Фиктивная компания для локального теста.", "observations": ["На публичной demo-странице указан общий business email.", "Публичный контактный путь ведёт на страницу контактов."], "potential_opportunities": ["Проверить путь записи на консультацию."], "personalization_points": ["Упомянуть спокойный digital customer experience."], "recommended_angle": f"Показать {sender.get('sender_brand') or 'пример'} как пример цифрового опыта.", "confidence": 0.91}
        else:
            payload = {"company": {k: candidate.get(k) for k in ("name", "city", "address", "website")}, "sources": candidate.get("sources", []), "contacts": contacts, "website_resolution": candidate.get("website_resolution", {"status": "WEBSITE_CANDIDATE", "evidence": [candidate.get("website")] }), "website_audit": candidate.get("website_audit", {}), "evidence": candidate.get("evidence") or candidate.get("website_audit", {}).get("evidence", [])}
            analysis = validate_analysis(payload, client.chat_opportunity(payload))
            snapshot = {"clinic": candidate, "public_site": {"text": site["text"], "pages": [{"url": p["url"], "text": p["text"][:1800]} for p in site["pages"]], "contact_sources": contacts}, "evidence_payload": payload}
    except Exception as exc:
        analysis = {"clinic_summary": "Локальный анализ LM Studio пока недоступен.", "observations": ["Ошибка соединения с LM Studio: " + str(exc)[:160]], "potential_opportunities": [], "personalization_points": [], "recommended_angle": "После запуска LM Studio повторить анализ.", "confidence": 0.0}
    # Normalize permissive local-model output without inventing facts.
    for key in ("observations", "potential_opportunities", "personalization_points"):
        value = analysis.get(key, [])
        if isinstance(value, dict): value = [f"{k}: {v}" for k, v in value.items()]
        elif isinstance(value, str): value = [value]
        analysis[key] = value
    confidence = analysis.get("confidence", "medium" if analysis.get("digital_state") and analysis.get("priority") else 0)
    if isinstance(confidence, str): confidence = {"high": 0.85, "medium": 0.65, "low": 0.35}.get(confidence.lower(), 0.5)
    try: confidence = float(confidence)
    except (TypeError, ValueError): confidence = 0.0
    analysis["confidence"] = max(0.0, min(1.0, confidence))
    if analysis["confidence"] == 0: return {"clinic_id": clinic_id, "status": "ANALYSIS_FAILED", "contacts": contacts}
    DB.add_analysis(clinic_id, client.model, snapshot, analysis)
    qualified_lead = {**candidate, "category": vertical.category, "contacts": contacts, "evidence": snapshot.get("evidence_payload", {}).get("evidence", candidate.get("evidence", []))}
    qualification = qualify_lead(qualified_lead, analysis, vertical)
    qualified_lead["qualification"] = qualification
    DB.update_clinic_status(clinic_id, qualification["status"], qualified_lead)
    if qualification["status"] != "QUALIFIED":
        return {"clinic_id": clinic_id, "status": qualification["status"], "qualification": qualification, "contacts": contacts, "analysis": analysis}
    try:
        if use_mock:
            draft = {"subject": f"Идея для цифрового опыта {candidate.get('name', 'компании')}", "body": "Здравствуйте!\n\nПосмотрел публичную demo-информацию и заметил возможность сделать digital customer experience понятнее — от первого контакта до заявки.\n\n" + (sender.get("sender_brand", "") + ": " + sender.get("demo_url", "") if sender.get("demo_url") else sender.get("offer", "")) + "\n\nЕсли тема неактуальна, просто ответьте на это письмо — я больше не буду вас беспокоить.", "rationale": "Mock-персонализация основана только на публичных demo-данных.", "source_observations": analysis["observations"], "confidence": analysis["confidence"]}
        else:
            draft = generate_draft(client, candidate, analysis, sender)
    except Exception as exc:
        print(f"[PIPELINE] draft generation failed for clinic_id={clinic_id}: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        return {"clinic_id": clinic_id, "status": "DRAFT_GENERATION_FAILED", "contacts": contacts, "analysis": analysis, "error": "Local model did not produce a valid draft."}
    valid, validation_reason = validate_dashboard_draft(draft, contacts, candidate.get("evidence", []) or [{"source": candidate.get("website", "")}], sender)
    if not valid:
        return {"clinic_id": clinic_id, "status": "DRAFT_VALIDATION_FAILED", "reason": validation_reason, "contacts": contacts, "analysis": analysis}
    draft = build_email_formats(draft)
    draft_id = DB.add_draft(clinic_id, contact_id, draft)
    return {"clinic_id": clinic_id, "draft_id": draft_id, "status": "DRAFTED", "contacts": contacts, "analysis": analysis}


def _discovery_worker(run_id: str, vertical_name: str, city: str, target_count: int) -> None:
    vertical = get_vertical(vertical_name)
    try:
        def on_activity(event: dict) -> None:
            status_map = {"SEARCH": "SEARCHING_SOURCES", "RESULT": "SEARCHING_SOURCES", "SOURCE_UNAVAILABLE": "SEARCHING_SOURCES", "RESOLUTION": "RESOLVING_DUPLICATES", "WEBSITE_AUDIT": "INSPECTING_WEBSITES", "CONTACT_SEARCH": "FINDING_PUBLIC_CONTACTS", "CONTACTS": "FINDING_PUBLIC_CONTACTS", "RESOLUTION_COMPLETE": "PERSISTING_RESULTS"}
            with DISCOVERY_LOCK:
                record = DISCOVERY_RUNS[run_id]
                record["activity"].append(event)
                if len(record["activity"]) > 100: record["activity"] = record["activity"][-100:]
                record["status"] = status_map.get(event.get("type"), record["status"])
                if event.get("type") == "RESULT": record["source_status"][event.get("provider")] = "SUCCESS"
                if event.get("type") == "SOURCE_UNAVAILABLE": record["source_status"][event.get("provider")] = "SOURCE_UNAVAILABLE"
                snapshot = dict(record)
            DB.save_discovery_result(run_id, snapshot)
            DB.update_autonomous_run(run_id, status=status_map.get(event.get("type"), "SEARCHING_SOURCES"))

        run = discover([DuckDuckGoProvider(), WebProvider()], vertical.key, build_search_query(vertical, city), target_count, audit_websites=True, on_activity=on_activity)
        saved = _persist_discovery_candidates(vertical, city, run.candidates)
        failed = not any(value == "SUCCESS" for value in run.source_status.values())
        status = "FAILED" if failed else "COMPLETE"
        result = {"run_id": run_id, "vertical": vertical.key, "city": city, "target_count": target_count, "status": status, "count": saved, "source_status": run.source_status, "candidates": run.candidates, "activity": run.activity, "error": "Все включённые источники недоступны." if failed else None}
        with DISCOVERY_LOCK: DISCOVERY_RUNS[run_id].update(result)
        DB.save_discovery_result(run_id, result)
        DB.update_autonomous_run(run_id, status=status, discovered=saved)
    except Exception as exc:
        print(f"[DISCOVERY] dashboard run={run_id} failed: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
        with DISCOVERY_LOCK:
            DISCOVERY_RUNS[run_id].update({"status": "FAILED", "error": "Не удалось завершить поиск. Проверьте доступность источников и повторите попытку."})
            result = dict(DISCOVERY_RUNS[run_id])
        DB.save_discovery_result(run_id, result)
        DB.update_autonomous_run(run_id, status="FAILED")


def _persist_discovery_candidates(vertical, city: str, candidates: list[dict]) -> int:
    saved = 0
    for lead in candidates:
        lead["category"] = vertical.category
        clinic_id = DB.add_clinic({"name": lead.get("name"), "website": lead.get("website"), "city": lead.get("city") or city, "category": vertical.category, "phone": lead.get("phone"), "source_url": lead.get("source_url"), "description": lead.get("description"), "profile": {**lead, "vertical": vertical.key, "status": "DISCOVERED"}})
        for contact in lead.get("contacts", []):
            if contact.get("email") and (contact.get("source_url") or contact.get("source")):
                DB.add_contact(clinic_id, contact["email"], contact.get("source_url") or contact.get("source"), contact.get("kind") or "PUBLIC_BUSINESS_EMAIL")
        saved += 1
    return saved


def _analyze_clinic(clinic_id: int, *, db: Database | None = None, force_analysis: bool = False, reuse_saved: bool = False, progress=None) -> tuple[int, dict]:
    db = db or DB
    detail = db.clinic_detail(clinic_id)
    if not detail: return 404, {"error": "Компания не найдена."}
    clinic, profile = detail["clinic"], detail["clinic"]["profile"]
    cached_analysis = detail.get("analysis") if reuse_saved and not force_analysis else None
    saved_snapshot = detail.get("analysis_snapshot") if cached_analysis else {}
    try:
        vertical = get_vertical(str(profile.get("vertical") or clinic.get("category") or "dental"))
    except ValueError:
        return 400, {"error": "Для компании не настроена вертикаль анализа."}
    contacts = list(profile.get("contacts") or [])
    contacts.extend({"email": item["email"], "type": item.get("contact_type", "PUBLIC_BUSINESS_EMAIL"), "source": item.get("source_url"), "source_url": item.get("source_url"), "confidence": item.get("confidence", "LOW")} for item in detail["contacts"] if item.get("email"))
    contacts = [item for item in contacts if isinstance(item, dict)]
    unique_contacts, seen_contacts = [], set()
    for item in contacts:
        key = (str(item.get("email") or "").lower(), str(item.get("phone") or ""), str(item.get("source") or item.get("source_url") or ""))
        if key not in seen_contacts: unique_contacts.append(item); seen_contacts.add(key)
    contacts = unique_contacts
    audit = profile.get("website_audit") or (saved_snapshot or {}).get("website_audit") or {}
    evidence = profile.get("evidence") or (saved_snapshot or {}).get("evidence") or audit.get("evidence") or []
    if not cached_analysis and not audit and clinic.get("website"):
        audit = audit_website(clinic["website"])
        evidence = audit.get("evidence", [])
    payload = {"company": {k: clinic.get(k) for k in ("name", "city", "website")}, "sources": profile.get("sources", (saved_snapshot or {}).get("sources", [])), "contacts": contacts, "website_resolution": profile.get("website_resolution", (saved_snapshot or {}).get("website_resolution", {})), "website_audit": audit, "evidence": evidence}
    env = load_env()
    lead = {**profile, "name": clinic.get("name"), "category": clinic.get("category"), "website": clinic.get("website"), "contacts": contacts, "evidence": payload["evidence"]}
    client = None
    if cached_analysis:
        analysis = cached_analysis
        qualification = profile.get("qualification")
        if not isinstance(qualification, dict) or qualification.get("status") not in {"QUALIFIED", "NEEDS_REVIEW", "DISQUALIFIED"}:
            qualification = qualify_lead(lead, analysis, vertical)
    else:
        if progress: progress("ANALYZING", f"{clinic.get('name')} — analysis started")
        try:
            client = lm_client(env)
            analysis = validate_analysis(payload, client.chat_opportunity(payload))
        except EvidenceValidationError as exc:
            print(f"[ANALYSIS] lead={clinic_id} evidence validation failed: {exc}", flush=True)
            return 422, {"error": "Результат AI не прошёл проверку evidence. Повторите анализ позже."}
        except Exception as exc:
            print(f"[ANALYSIS] lead={clinic_id} failed: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
            return 503, {"error": "Не удалось завершить анализ. Проверьте, что LM Studio запущен и модель доступна."}
        qualification = qualify_lead(lead, analysis, vertical)
        db.add_analysis(clinic_id, client.model, payload, analysis)

    lead["qualification"], lead["vertical"] = qualification, vertical.key
    draft_warning = None
    db.update_clinic_status(clinic_id, qualification["status"], lead)
    if qualification["status"] == "QUALIFIED":
        existing = db.active_draft(clinic_id)
        if existing:
            lead["draft_id"] = existing["id"]
            db.update_clinic_status(clinic_id, "READY_TO_SEND" if existing["status"] == "APPROVED" else "LETTER_DONE", lead)
        else:
            email_contact = next((item for item in contacts if item.get("email") and (item.get("source_url") or item.get("source"))), None)
            if not email_contact:
                draft_warning = "Публичный email с provenance не найден; draft не создавался."
            else:
                email = str(email_contact["email"]).strip().lower()
                if db.is_suppressed(email):
                    draft_warning = "Адрес находится в suppression list; draft не создавался."
                    email_contact = None
                if email_contact:
                    contact_id = db.add_contact(clinic_id, email, str(email_contact.get("source_url") or email_contact.get("source")), str(email_contact.get("type") or "PUBLIC_BUSINESS_EMAIL"))
                    if not contact_id:
                        draft_warning = "Для email найден конфликт контактов; draft оставлен на ручную проверку."
                    else:
                        try:
                            if progress: progress("GENERATING_LETTER", f"{clinic.get('name')} — generating letter")
                            if client is None: client = lm_client(env)
                            sender = config(env, vertical.key)
                            draft = generate_draft(client, lead, analysis, sender)
                            valid, reason = validate_dashboard_draft(draft, contacts, payload["evidence"], sender)
                            if not valid: raise ValueError(reason)
                            draft = build_email_formats(draft)
                            lead["draft_id"] = db.add_draft(clinic_id, contact_id, draft)
                            db.update_clinic_status(clinic_id, "LETTER_DONE", lead)
                        except Exception as exc:
                            print(f"[DRAFT] lead={clinic_id} generation failed: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
                            draft_warning = "Анализ сохранён, но локальная модель не создала draft. Повторите позже."
    fresh = db.clinic_detail(clinic_id)
    return 200, {"analysis": fresh["analysis"], "qualification": qualification, "draft": fresh["draft"], "draft_warning": draft_warning}


def _batch_worker(batch_id: str) -> None:
    db = Database(DB.path)
    try:
        batch = db.get_batch_job(batch_id)
        if not batch: return
        for item in batch["items"]:
            if item["status"] != "PENDING": continue
            clinic_id = int(item["clinic_id"])
            with ANALYSIS_LOCK:
                if clinic_id in ANALYZING_LEADS:
                    db.update_batch_item(batch_id, clinic_id, status="FAILED", stage="FAILED", error="Another analysis is already running for this company.", finished_at=utc_now())
                    db.add_job_event("analysis", batch_id, "CLINIC_FAILED", f"{item['company_name']} — FAILED · analysis already in progress", clinic_id, company_name=item["company_name"])
                    continue
                ANALYZING_LEADS.add(clinic_id)
            db.update_batch_item(batch_id, clinic_id, status="ANALYZING", stage="ANALYZING", started_at=utc_now())
            db.add_job_event("analysis", batch_id, "ANALYSIS_STARTED", f"{item['company_name']} — analysis started", clinic_id, company_name=item["company_name"])

            def progress(stage: str, message: str) -> None:
                db.update_batch_item(batch_id, clinic_id, stage=stage)
                db.add_job_event("analysis", batch_id, stage, message, clinic_id, company_name=item["company_name"])

            try:
                status, result = _analyze_clinic(clinic_id, db=db, reuse_saved=True, progress=progress)
                response_status = str((result.get("qualification") or {}).get("status") or result.get("status") or "")
                failed_draft = bool(result.get("draft_warning") and "локальная модель не создала draft" in result["draft_warning"].lower())
                failed = status >= 400 or response_status.endswith("FAILED") or failed_draft
                if failed:
                    reason = str(result.get("error") or result.get("draft_warning") or "Analysis could not be completed.")
                    db.update_batch_item(batch_id, clinic_id, status="FAILED", stage="FAILED", error=reason, finished_at=utc_now())
                    db.add_job_event("analysis", batch_id, "CLINIC_FAILED", f"{item['company_name']} — FAILED · {reason}", clinic_id, company_name=item["company_name"])
                else:
                    db.update_batch_item(batch_id, clinic_id, status="COMPLETED", stage="COMPLETED", finished_at=utc_now())
                    qualification = result.get("qualification") or {}
                    db.add_job_event("analysis", batch_id, qualification.get("status", "ANALYSIS_COMPLETED"), f"{item['company_name']} — {qualification.get('status', 'analysis completed')}", clinic_id, company_name=item["company_name"])
                    if result.get("draft"):
                        db.add_job_event("analysis", batch_id, "LETTER_DONE", f"{item['company_name']} — letter generated", clinic_id, company_name=item["company_name"])
            except Exception as exc:
                print(f"[BATCH] {batch_id} clinic={clinic_id} failed: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
                reason = "Не удалось обработать клинику. Подробности сохранены в серверном логе."
                db.update_batch_item(batch_id, clinic_id, status="FAILED", stage="FAILED", error=reason, finished_at=utc_now())
                db.add_job_event("analysis", batch_id, "CLINIC_FAILED", f"{item['company_name']} — FAILED · {reason}", clinic_id, company_name=item["company_name"])
            finally:
                with ANALYSIS_LOCK: ANALYZING_LEADS.discard(clinic_id)
        db.finish_batch_job(batch_id, "COMPLETED")
    except Exception as exc:
        print(f"[BATCH] run={batch_id} failed: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
        try: db.finish_batch_job(batch_id, "FAILED")
        except Exception: pass
    finally:
        db.close()


def _setting_int(env: dict[str, str], key: str, default: int, low: int = 0, high: int = 86400) -> int:
    try: value = int(env.get(key, default))
    except (TypeError, ValueError): value = default
    return max(low, min(high, value))


def _smtp_configuration_ready(env: dict[str, str]) -> bool:
    return env.get("SMTP_ENABLED", "false").lower() == "true" and all(env.get(key) for key in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM"))


def _safe_smtp_error(exc: Exception) -> tuple[str, str, bool]:
    """Return (safe reason, stop category, provider outcome may be uncertain)."""
    if isinstance(exc, smtplib.SMTPAuthenticationError): return "SMTP authentication failed; sending stopped.", "STOPPED_AUTH_ERROR", False
    if isinstance(exc, smtplib.SMTPRecipientsRefused): return "Provider rejected a recipient; sending stopped.", "STOPPED_PROVIDER_REJECTION", False
    if isinstance(exc, smtplib.SMTPResponseException):
        code = int(getattr(exc, "smtp_code", 0) or 0)
        text = str(getattr(exc, "smtp_error", b"")).lower()
        if code in {421, 429, 450, 451, 452} or any(term in text for term in ("rate limit", "too many", "throttl", "try again later")):
            return f"Provider rate limit/rejection (SMTP {code}); sending stopped and cooldown applied.", "STOPPED_PROVIDER_LIMIT", False
        return f"Provider rejected a message (SMTP {code}); sending stopped.", "STOPPED_PROVIDER_REJECTION", False
    if isinstance(exc, (TimeoutError, ConnectionError, OSError, smtplib.SMTPServerDisconnected)):
        return "Connection ended before delivery could be confirmed; this draft is isolated from automatic retries.", "SEND_INTERRUPTED", True
    return "Provider error; the draft failed and will not be retried automatically.", "FAILED", False


def _send_worker(send_id: str) -> None:
    db = Database(DB.path)
    try:
        job = db.get_send_job(send_id)
        if not job: return
        mode = job["mode"]
        provider = SimulatedProvider() if mode == "SIMULATED_SEND" else SMTPProvider(load_env())
        if mode == "REAL_SMTP":
            try:
                provider.check_available()
            except smtplib.SMTPAuthenticationError:
                db.finish_send_job(send_id, "STOPPED_AUTH_ERROR", "SMTP authentication failed; no messages were attempted.")
                return
            except Exception as exc:
                print(f"[SEND] preflight failed: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
                db.finish_send_job(send_id, "STOPPED_PROVIDER_UNAVAILABLE", "SMTP provider could not be reached/authenticated; no messages were attempted.")
                return

        env = load_env()
        consecutive_errors = 0
        max_errors = _setting_int(env, "SMTP_MAX_CONSECUTIVE_ERRORS", 3, 1, 20)
        cooldown_seconds = _setting_int(env, "SMTP_COOLDOWN_SECONDS", 1800, 1, 604800)
        daily_limit = int(job["daily_limit"])
        today = datetime.now(timezone.utc).date().isoformat()
        sent_today = int(db.conn.execute("SELECT COUNT(*) FROM send_logs WHERE status='SENT' AND created_at>=?", (today + "T00:00:00",)).fetchone()[0])
        daily_remaining = max(0, daily_limit - sent_today)
        pending = [item for item in job["items"] if item["status"] == "PENDING"][:daily_remaining]
        excess = [item for item in job["items"] if item["status"] == "PENDING"][daily_remaining:]
        for item in excess:
            db.update_send_item(send_id, int(item["draft_id"]), status="SKIPPED", reason="Provider daily send limit reached.", finished_at=utc_now())
            db.add_job_event("send", send_id, "SKIPPED", f"{item['company_name']} — SKIPPED · provider daily limit reached", int(item["clinic_id"]), int(item["draft_id"]), str(item["company_name"]))

        terminal = None
        for index, queued in enumerate(pending):
            draft_id = int(queued["draft_id"])
            with SEND_LOCK:
                if draft_id in SENDING_DRAFTS:
                    db.update_send_item(send_id, draft_id, status="SKIPPED", reason="This draft already has an active send lock.", finished_at=utc_now())
                    continue
                SENDING_DRAFTS.add(draft_id)
            try:
                reserved, reason = db.reserve_send_item(send_id, draft_id)
                if not reserved:
                    db.update_send_item(send_id, draft_id, status="SKIPPED", reason=reason, finished_at=utc_now())
                    db.add_job_event("send", send_id, "SKIPPED", f"{queued['company_name']} — SKIPPED · {reason}", int(queued["clinic_id"]), draft_id, str(queued["company_name"]))
                    continue
                try:
                    provider.send(reserved["recipient"], reserved["subject"], reserved["body"], reserved.get("html_body"))
                    db.complete_send_attempt(send_id, draft_id, int(reserved["send_log_id"]), "SENT" if mode == "REAL_SMTP" else "SIMULATED_SENT")
                    consecutive_errors = 0
                except Exception as exc:
                    print(f"[SEND] batch={send_id} draft={draft_id} failed: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
                    reason, stop_status, uncertain = _safe_smtp_error(exc)
                    db.complete_send_attempt(send_id, draft_id, int(reserved["send_log_id"]), "UNCERTAIN" if uncertain else "FAILED", reason)
                    consecutive_errors += 1
                    if stop_status == "STOPPED_PROVIDER_LIMIT":
                        until = datetime.now(timezone.utc).timestamp() + cooldown_seconds
                        cooldown_until = datetime.fromtimestamp(until, timezone.utc).isoformat(timespec="seconds")
                        db.set_provider_cooldown(cooldown_until, reason)
                        terminal = stop_status
                    elif stop_status != "FAILED":
                        terminal = stop_status
                    elif consecutive_errors >= max_errors:
                        terminal = "STOPPED_REPEATED_ERRORS"
                    if terminal: break
                if index < len(pending) - 1 and not terminal and int(job["max_delay_seconds"]) > 0:
                    delay = random.randint(int(job["min_delay_seconds"]), int(job["max_delay_seconds"]))
                    next_company = pending[index + 1]["company_name"]
                    db.conn.execute("UPDATE send_jobs SET current_company=?,current_stage='WAITING' WHERE send_id=?", (next_company, send_id)); db.conn.commit()
                    db.add_job_event("send", send_id, "WAITING", f"Waiting {delay} sec for provider rate limits before next message", company_name=str(next_company))
                    time.sleep(delay)
            finally:
                with SEND_LOCK: SENDING_DRAFTS.discard(draft_id)
        final_status = terminal or ("COMPLETED" if not any(item["status"] == "PENDING" for item in db.get_send_job(send_id)["items"]) else "SEND_INTERRUPTED")
        db.finish_send_job(send_id, final_status)
    except Exception as exc:
        print(f"[SEND] batch={send_id} failed: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
        try: db.finish_send_job(send_id, "FAILED", "Send batch stopped after a server-side error.")
        except Exception: pass
    finally:
        db.close()


class Handler(BaseHTTPRequestHandler):
    def json(self, status: int, payload: dict):
        raw = json.dumps(payload, ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/dashboard": return self.json(200, DB.dashboard())
        if path == "/api/verticals": return self.json(200, {"items": [{"key": item.key, "label": item.label} for item in VERTICALS.values()]})
        if path == "/api/batches/latest": return self.json(200, {"job": DB.latest_batch_job()})
        if path == "/api/batches/eligible":
            query = parse_qs(urlparse(self.path).query)
            vertical = str((query.get("vertical") or ["dental"])[0])
            try: get_vertical(vertical)
            except ValueError: return self.json(400, {"error": "Неизвестная вертикаль."})
            return self.json(200, {"vertical": vertical, "available": len(DB.analysis_candidates(vertical, 10000))})
        batch_match = re.fullmatch(r"/api/batches/([A-Za-z0-9-]+)", path)
        if batch_match:
            job = DB.get_batch_job(batch_match.group(1))
            return self.json(200, {"job": job}) if job else self.json(404, {"error": "Batch не найден."})
        if path == "/api/send-batches/latest": return self.json(200, {"job": DB.latest_send_job()})
        if path == "/api/send-batches/ready":
            query = parse_qs(urlparse(self.path).query)
            vertical = str((query.get("vertical") or [""])[0]) or None
            return self.json(200, {"count": len(DB.ready_to_send(vertical))})
        send_match = re.fullmatch(r"/api/send-batches/([A-Za-z0-9-]+)", path)
        if send_match:
            job = DB.get_send_job(send_match.group(1))
            return self.json(200, {"job": job}) if job else self.json(404, {"error": "Send batch не найден."})
        if path == "/api/discovery/latest":
            persisted = DB.latest_discovery_run()
            if not persisted: return self.json(200, {"run": None})
            with DISCOVERY_LOCK: current = DISCOVERY_RUNS.get(persisted["run_id"])
            result = current or persisted
            if current is None and result.get("status") not in {"COMPLETE", "FAILED", "INTERRUPTED"}:
                result = {**result, "status": "INTERRUPTED", "error": "Сервер перезапустился до завершения поиска; показан сохранённый результат."}
            return self.json(200, {"run": result})
        discovery_match = re.fullmatch(r"/api/discovery/([A-Za-z0-9-]+)", path)
        if discovery_match:
            with DISCOVERY_LOCK: result = DISCOVERY_RUNS.get(discovery_match.group(1))
            if result is None:
                result = DB.get_discovery_run(discovery_match.group(1))
                if result and result.get("status") not in {"COMPLETE", "FAILED", "INTERRUPTED"}:
                    result = {**result, "status": "INTERRUPTED", "error": "Сервер перезапустился до завершения поиска; показан сохранённый результат."}
            return self.json(200, result) if result else self.json(404, {"error": "Discovery run not found."})
        clinic_match = re.fullmatch(r"/api/clinics/(\d+)", path)
        if clinic_match:
            result = DB.clinic_detail(int(clinic_match.group(1)))
            return self.json(200, result) if result else self.json(404, {"error": "Компания не найдена."})
        if path == "/api/queue":
            items = DB.list_queue()
            ready_ids = {int(row["id"]) for row in DB.ready_to_send()}
            for item in items:
                detail = DB.clinic_detail(int(item["clinic_id"]))
                item["workflow_status"] = "READY_TO_SEND" if int(item["id"]) in ready_ids else ("LETTER_DONE" if item["status"] == "DRAFTED" else "APPROVED")
                item["analysis"] = detail.get("analysis") if detail else None
                item["qualification"] = (detail.get("clinic", {}).get("profile", {}).get("qualification") or {}) if detail else {}
                item["evidence"] = detail.get("evidence", []) if detail else []
                item["contacts"] = detail.get("contacts", []) if detail else []
            return self.json(200, {"items": items})
        if path == "/api/clinics": return self.json(200, {"items": DB.list_clinics()})
        if path == "/api/contacts": return self.json(200, {"items": DB.list_contacts()})
        if path == "/api/approved": return self.json(200, {"items": DB.list_queue("APPROVED")})
        if path == "/api/sent": return self.json(200, {"items": DB.list_send_logs()})
        if path == "/api/suppression": return self.json(200, {"items": DB.list_suppression()})
        if path == "/api/activity":
            items = DB.recent_activity()
            items.extend({"type": event["event"], "clinic_name": event.get("company_name") or "Batch", "message": event["message"], "timestamp": event["created_at"]} for event in DB.job_activity())
            with DISCOVERY_LOCK:
                for run in DISCOVERY_RUNS.values():
                    items.extend({"type": f"DISCOVERY_{event.get('type', 'EVENT')}", "clinic_name": event.get("company") or event.get("provider") or "Discovery", "message": event.get("message") or event.get("error") or event.get("type"), "timestamp": ""} for event in run.get("activity", []))
            items.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
            return self.json(200, {"items": items[:60]})
        if path == "/api/settings":
            env = load_env(); client = LMStudioClient(env.get("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1"), "qwen/qwen3-vl-8b")
            current_config = config(env)
            smtp_enabled = env.get("SMTP_ENABLED", "false").lower() == "true"
            return self.json(200, {"lm_studio_url": client.base_url, "lm_studio_model": client.model, "smtp_enabled": smtp_enabled, "smtp_configured": _smtp_configuration_ready(env), "provider": "smtp" if smtp_enabled else "simulated", "smtp_host": "", "smtp_port": "", "sender_email": env.get("SMTP_FROM", env.get("SENDER_EMAIL", "")), "real_sends": int(DB.conn.execute("SELECT COUNT(*) FROM send_logs WHERE provider='smtp' AND status='SENT'").fetchone()[0]), "max_sends_per_batch": _setting_int(env, "MAX_SENDS_PER_BATCH", 50, 1, 500), "daily_send_limit": _setting_int(env, "SMTP_DAILY_LIMIT", 50, 1, 10000), "min_send_delay_seconds": _setting_int(env, "SMTP_MIN_DELAY_SECONDS", 30, 1, 86400), "cooldown": DB.provider_cooldown(), **current_config})
        if path == "/api/health":
            env=load_env()
            capabilities = {"verticals": True, "discovery": True, "batch_analysis": True, "send_batch": True}
            try:
                client=lm_client(env)
                return self.json(200, {"lm_studio": True, "models": client.health(), "capabilities": capabilities})
            except Exception as exc:
                print(f"[HEALTH] LM Studio unavailable: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
                return self.json(200, {"lm_studio": False, "error": "LM Studio недоступен или настроенная модель не загружена.", "capabilities": capabilities})
        if path == "/":
            raw=UI.read_bytes(); self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if path == "/review.js":
            raw=(UI.parent / "review.js").read_bytes(); self.send_response(200); self.send_header("Content-Type","application/javascript; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if path == "/review.css":
            raw=(UI.parent / "review.css").read_bytes(); self.send_response(200); self.send_header("Content-Type","text/css; charset=utf-8"); self.send_header("Content-Length",str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        self.send_error(404)
    def body(self): return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
    def do_POST(self):
        path=urlparse(self.path).path; data=self.body()
        if path=="/api/discovery/start":
            try:
                vertical = get_vertical(str(data.get("vertical", "dental")))
                city = str(data.get("city", "Астана")).strip()
                target = discovery_limit(data.get("target_count", 10))
                if not city: raise ValueError("City is required.")
            except (ValueError, TypeError) as exc:
                return self.json(400, {"error": "Проверьте вертикаль, город и количество компаний."})
            run_id = f"disc-{uuid.uuid4().hex[:12]}"
            DB.create_discovery_run(run_id, vertical.key, target, "duckduckgo+web")
            with DISCOVERY_LOCK:
                DISCOVERY_RUNS[run_id] = {"run_id": run_id, "vertical": vertical.key, "city": city, "target_count": target, "status": "SEARCHING_SOURCES", "count": 0, "activity": [], "source_status": {}, "candidates": []}
            threading.Thread(target=_discovery_worker, args=(run_id, vertical.key, city, target), name=f"discovery-{run_id}", daemon=True).start()
            return self.json(202, {"run_id": run_id, "status": "SEARCHING_SOURCES"})
        if path=="/api/discover":
            try:
                vertical = get_vertical(str(data.get("vertical", "dental")))
                city = str(data.get("city", "Астана")).strip()
                target = discovery_limit(data.get("target_count", 10))
                if not city: raise ValueError("City is required.")
            except (ValueError, TypeError):
                return self.json(400, {"error": "Проверьте вертикаль, город и количество компаний."})
            if data.get("mock"):
                candidates = mock_candidates()[:target]
                return self.json(200, {"candidates": candidates, "count": len(candidates), "sources": ["mock"]})
            if not data.get("vertical") and not data.get("real") and city.lower() not in {"астана", "astana"}:
                candidates = discover_candidates(data.get("city", ""), data.get("country", ""), data.get("category", ""), data.get("keywords", ""))
                return self.json(200, {"candidates": candidates, "count": len(candidates), "sources": ["public_search"]})
            run_id = f"disc-{uuid.uuid4().hex[:12]}"
            DB.create_discovery_run(run_id, vertical.key, target, "duckduckgo+web")
            run = discover([DuckDuckGoProvider(), WebProvider()], vertical.key, build_search_query(vertical, city), target, audit_websites=True)
            saved = _persist_discovery_candidates(vertical, city, run.candidates)
            failed = not any(value == "SUCCESS" for value in run.source_status.values())
            result = {"run_id": run_id, "vertical": vertical.key, "city": city, "target_count": target, "status": "FAILED" if failed else "COMPLETE", "count": saved, "source_status": run.source_status, "candidates": run.candidates, "activity": run.activity, "error": "Все включённые источники недоступны." if failed else None}
            DB.save_discovery_result(run_id, result)
            DB.update_autonomous_run(run_id, status=result["status"], discovered=saved)
            return self.json(200, {"candidates": run.candidates, "count": len(run.candidates), "saved": saved, "run_id": run_id, "status": result["status"], "sources": [name for name, status in run.source_status.items() if status == "SUCCESS"], "source_status": run.source_status, "activity": run.activity, "error": result["error"]})
        analysis_match = re.fullmatch(r"/api/clinics/(\d+)/analyze", path)
        if analysis_match:
            clinic_id = int(analysis_match.group(1))
            with ANALYSIS_LOCK:
                if clinic_id in ANALYZING_LEADS:
                    return self.json(409, {"code": "ANALYSIS_ALREADY_RUNNING", "error": "Анализ этой компании уже выполняется."})
                ANALYZING_LEADS.add(clinic_id)
            try:
                repeat = bool(data.get("repeat"))
                status, result = _analyze_clinic(clinic_id, force_analysis=repeat, reuse_saved=not repeat)
                return self.json(status, result)
            finally:
                with ANALYSIS_LOCK: ANALYZING_LEADS.discard(clinic_id)
        if path == "/api/batches/start":
            try:
                vertical = get_vertical(str(data.get("vertical", "dental")))
                requested = int(data.get("requested_count", 20))
                if not 1 <= requested <= 100: raise ValueError
            except (ValueError, TypeError):
                return self.json(400, {"error": "Укажите vertical и количество от 1 до 100."})
            with BATCH_LOCK:
                active = DB.conn.execute("SELECT batch_id FROM batch_jobs WHERE status='RUNNING' LIMIT 1").fetchone()
                if active: return self.json(409, {"code": "BATCH_ALREADY_RUNNING", "batch_id": active[0], "error": "Анализ batch уже выполняется."})
                clinics = DB.analysis_candidates(vertical.key, requested)
                batch_id = f"batch-{uuid.uuid4().hex[:12]}"
                try: DB.create_batch_job(batch_id, vertical.key, requested, clinics)
                except Exception as exc:
                    print(f"[BATCH] Could not create run: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
                    return self.json(409, {"code": "BATCH_ALREADY_RUNNING", "error": "Другой analysis batch уже запущен."})
                threading.Thread(target=_batch_worker, args=(batch_id,), name=f"analysis-batch-{batch_id}", daemon=True).start()
            return self.json(202, {"batch_id": batch_id, "status": "RUNNING", "requested_count": requested, "queued_count": len(clinics)})
        batch_resume = re.fullmatch(r"/api/batches/([A-Za-z0-9-]+)/resume", path)
        if batch_resume:
            batch_id = batch_resume.group(1)
            with BATCH_LOCK:
                active = DB.conn.execute("SELECT batch_id FROM batch_jobs WHERE status='RUNNING' LIMIT 1").fetchone()
                if active: return self.json(409, {"code": "BATCH_ALREADY_RUNNING", "batch_id": active[0], "error": "Другой analysis batch уже выполняется."})
                if not DB.resume_batch_job(batch_id): return self.json(409, {"error": "Batch не прерван или в нём не осталось необработанных клиник."})
                threading.Thread(target=_batch_worker, args=(batch_id,), name=f"analysis-batch-{batch_id}", daemon=True).start()
            return self.json(202, {"batch_id": batch_id, "status": "RUNNING"})
        if path=="/api/pipeline": return self.json(200,run_pipeline(data.get("candidate") or mock_candidates()[0], bool(data.get("mock"))))
        if path=="/api/run-real":
            try: vertical = get_vertical(str(data.get("vertical", "dental")))
            except ValueError: return self.json(400, {"error": "Неизвестная вертикаль."})
            records = curated_candidates(vertical.key)
            if not records: return self.json(400, {"error": f"Для vertical {vertical.key} нет curated records."})
            candidates = [{**c, "category": vertical.category, "vertical": vertical.key, "precollected": True, "profile": c} for c in records[:int(data.get("target_count", 10))]]
            results = [run_pipeline(c, False) for c in candidates]
            return self.json(200, {"status": "REAL_DISCOVERY_COMPLETE", "count": len(results), "drafts": sum(1 for r in results if r.get("draft_id")), "results": results})
        draft_regen = re.fullmatch(r"/api/clinics/(\d+)/draft/regenerate", path)
        if draft_regen:
            clinic_id = int(draft_regen.group(1)); detail = DB.clinic_detail(clinic_id)
            if not detail: return self.json(404, {"error": "Компания не найдена."})
            clinic, profile = detail["clinic"], detail["clinic"]["profile"]
            qualification = profile.get("qualification") or {}
            if qualification.get("status") != "QUALIFIED" or not detail.get("analysis"):
                return self.json(409, {"error": "Повторная генерация возможна только для проанализированной и квалифицированной компании."})
            if clinic.get("status") in {"SENT", "SENDING", "DO_NOT_CONTACT", "SEND_INTERRUPTED"}:
                return self.json(409, {"error": "Для этой компании генерация письма заблокирована текущим статусом."})
            contact = next((item for item in detail["contacts"] if item.get("email") and item.get("contact_type") == "PUBLIC_BUSINESS_EMAIL" and item.get("is_public") and item.get("source_url")), None)
            if not contact: return self.json(409, {"error": "Подтверждённый публичный business email с источником не найден."})
            if DB.is_suppressed(contact["email"]): return self.json(409, {"error": "Адрес находится в suppression list."})
            vertical = get_vertical(str(profile.get("vertical") or clinic.get("category") or "dental"))
            lead = {**profile, "name": clinic["name"], "category": clinic.get("category"), "website": clinic.get("website"), "contacts": detail["contacts"], "evidence": detail.get("evidence", [])}
            try:
                client = lm_client(load_env()); sender = config(load_env(), vertical.key)
                draft = generate_draft(client, lead, detail["analysis"], sender)
                valid, reason = validate_dashboard_draft(draft, lead["contacts"], lead["evidence"], sender)
                if not valid: raise ValueError(reason)
                draft = build_email_formats(draft)
                draft_id = DB.add_draft(clinic_id, int(contact["id"]), draft, force=True)
                DB.update_clinic_status(clinic_id, "LETTER_DONE", profile)
                DB.add_job_event("analysis", f"clinic-{clinic_id}", "LETTER_REGENERATED", f"{clinic['name']} — letter regenerated", clinic_id, draft_id, clinic["name"])
                return self.json(200, {"draft_id": draft_id, "status": "LETTER_DONE", "draft": DB.clinic_detail(clinic_id)["draft"]})
            except Exception as exc:
                print(f"[DRAFT] regeneration failed clinic={clinic_id}: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
                return self.json(503, {"error": "Не удалось повторно сгенерировать письмо. Проверьте LM Studio и повторите позже."})
        if path=="/api/drafts/update": DB.update_draft(int(data["id"]),subject=data.get("subject",""),body=data.get("body","")); return self.json(200,{"ok":True})
        if path=="/api/drafts/status":
            status = data["status"]
            if status not in {"DRAFTED", "APPROVED", "SKIPPED", "DO_NOT_CONTACT"}: return self.json(400, {"error": "Недопустимый статус draft."})
            row = DB.get_draft(int(data["id"]))
            if not row or row["status"] in {"SENT", "DO_NOT_CONTACT"}: return self.json(409, {"error": "Этот draft больше нельзя approve/reopen."})
            if row["status"] == status: return self.json(200,{"ok":True})
            DB.update_draft(int(data["id"]),status=status)
            if status in {"DRAFTED", "APPROVED"}:
                lifecycle = "LETTER_DONE" if status == "DRAFTED" else "APPROVED"
                DB.update_clinic_status(int(row["clinic_id"]), lifecycle)
                if status == "APPROVED" and any(int(item["id"]) == int(row["id"]) for item in DB.ready_to_send()):
                    DB.update_clinic_status(int(row["clinic_id"]), "READY_TO_SEND")
            return self.json(200,{"ok":True})
        if path=="/api/suppress": DB.suppress(data["email"],data.get("reason","manual")); return self.json(200,{"ok":True})
        if path=="/api/suppression/remove": DB.remove_suppression(data["email"]); return self.json(200,{"ok":True})
        if path == "/api/send-batches/preview":
            try:
                vertical = get_vertical(str(data.get("vertical", "dental")))
                requested = int(data.get("count", 10))
                mode = str(data.get("mode", "REAL_SMTP"))
                minimum = int(data.get("min_delay_seconds", 45)); maximum = int(data.get("max_delay_seconds", 120))
                if mode not in {"REAL_SMTP", "SIMULATED_SEND"} or not 1 <= requested <= 500: raise ValueError
                if not 0 <= minimum <= maximum <= 86400: raise ValueError
            except (ValueError, TypeError): return self.json(400, {"error": "Проверьте vertical, количество писем и интервал."})
            active = DB.active_send_job()
            if active: return self.json(409, {"code": "SEND_ALREADY_RUNNING", "send_id": active["send_id"], "error": "Send batch уже выполняется или ждёт явного продолжения."})
            env = load_env()
            if mode == "REAL_SMTP":
                if not _smtp_configuration_ready(env): return self.json(403, {"error": "Реальная отправка недоступна: SMTP отключён или конфигурация неполная."})
                minimum_delay = _setting_int(env, "SMTP_MIN_DELAY_SECONDS", 30, 1, 86400)
                if minimum < minimum_delay: return self.json(400, {"error": f"Для этого провайдера минимальный интервал — {minimum_delay} секунд."})
            else:
                minimum_delay = 0
            cooldown = DB.provider_cooldown()
            if mode == "REAL_SMTP" and cooldown and cooldown["cooldown_until"] > datetime.now(timezone.utc).isoformat(timespec="seconds"):
                return self.json(429, {"error": f"Провайдер на cooldown до {cooldown['cooldown_until']}. Причина: {cooldown['reason']}"})
            max_per_batch = _setting_int(env, "MAX_SENDS_PER_BATCH", 50, 1, 500)
            daily_limit = _setting_int(env, "SMTP_DAILY_LIMIT", 50, 1, 10000)
            today = datetime.now(timezone.utc).date().isoformat()
            sent_today = int(DB.conn.execute("SELECT COUNT(*) FROM send_logs WHERE provider='smtp' AND status='SENT' AND created_at>=?", (today + "T00:00:00",)).fetchone()[0])
            daily_remaining = max(0, daily_limit - sent_today)
            eligible = DB.ready_to_send(vertical.key)
            planned = min(requested, max_per_batch, daily_remaining, len(eligible))
            if planned < 1: return self.json(409, {"error": "Нет одобренных публичных бизнес-писем, доступных для отправки в заданной vertical/с учётом лимитов.", "ready_count": len(eligible), "daily_remaining": daily_remaining, "max_per_batch": max_per_batch})
            selected = eligible[:planned]
            if mode == "REAL_SMTP" and maximum < minimum_delay: return self.json(400, {"error": f"Интервал не может быть меньше {minimum_delay} секунд."})
            send_id = f"send-{uuid.uuid4().hex[:12]}"
            try: DB.create_send_job(send_id, vertical.key, mode, requested, minimum, maximum, max_per_batch, daily_limit, selected)
            except Exception as exc:
                print(f"[SEND] Could not create preview: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
                return self.json(409, {"code": "SEND_ALREADY_RUNNING", "error": "Не удалось создать preview: другой send job уже выполняется."})
            return self.json(200, {"send_id": send_id, "ready_count": len(eligible), "planned_count": planned, "requested_count": requested, "mode": mode, "vertical": vertical.key, "min_delay_seconds": minimum, "max_delay_seconds": maximum, "max_per_batch": max_per_batch, "daily_remaining": daily_remaining, "status": "PREVIEW"})
        send_confirm = re.fullmatch(r"/api/send-batches/([A-Za-z0-9-]+)/(confirm|resume)", path)
        if send_confirm:
            send_id, action = send_confirm.group(1), send_confirm.group(2)
            with SEND_LOCK:
                active = DB.active_send_job()
                if active and active["send_id"] != send_id:
                    return self.json(409, {"code": "SEND_ALREADY_RUNNING", "send_id": active["send_id"], "error": "Другой send batch уже выполняется."})
                job = DB.get_send_job(send_id)
                if not job: return self.json(404, {"error": "Send batch не найден."})
                if action == "confirm":
                    if job["status"] != "PREVIEW": return self.json(409, {"error": "Preview устарел; создайте новый перед отправкой."})
                    if job["mode"] == "REAL_SMTP" and not _smtp_configuration_ready(load_env()): return self.json(403, {"error": "SMTP отключён или конфигурация неполная."})
                    eligible_ids = {int(item["id"]) for item in DB.ready_to_send(job["vertical"])}
                    for item in job["items"]:
                        if int(item["draft_id"]) not in eligible_ids:
                            DB.update_send_item(send_id, int(item["draft_id"]), status="SKIPPED", reason="Draft no longer passes the send safety checks.", finished_at=utc_now())
                    if not any(item["status"] == "PENDING" and int(item["draft_id"]) in eligible_ids for item in DB.get_send_job(send_id)["items"]):
                        DB.finish_send_job(send_id, "CANCELLED", "No previewed draft remains eligible.")
                        return self.json(409, {"error": "После повторной проверки писем, пригодных для отправки, не осталось."})
                elif job["status"] != "SEND_INTERRUPTED":
                    return self.json(409, {"error": "Этот send batch не прерван и не требует продолжения."})
                if not DB.confirm_send_job(send_id): return self.json(409, {"code": "SEND_ALREADY_RUNNING", "error": "Send batch уже запущен."})
                threading.Thread(target=_send_worker, args=(send_id,), name=f"send-batch-{send_id}", daemon=True).start()
            return self.json(202, {"send_id": send_id, "status": "RUNNING", "action": action})
        if path=="/api/send":
            draft_id = int(data["id"])
            if data.get("simulate") is False: return self.json(403, {"error": "Real SMTP send требует сохранённый send preview и отдельное явное подтверждение."})
            with SEND_LOCK:
                if draft_id in SENDING_DRAFTS or DB.active_send_job(): return self.json(409, {"code": "SEND_ALREADY_RUNNING", "error": "Send уже выполняется; повторный запуск заблокирован."})
                SENDING_DRAFTS.add(draft_id)
            try:
                row=DB.get_draft(draft_id)
                if not row or row["status"]!="APPROVED": return self.json(409,{"error":"Отправка разрешена только для явно APPROVED draft."})
                if row["clinic_status"] in {"DO_NOT_CONTACT", "SENT", "SEND_FAILED", "SEND_INTERRUPTED"}: return self.json(409,{"error":"Компания не проходит safety status для отправки."})
                if row["contact_type"] != "PUBLIC_BUSINESS_EMAIL" or not row["is_public"] or not str(row["email_source"] or "").startswith(("http://", "https://")): return self.json(409,{"error":"Нужен подтверждённый публичный business email с источником."})
                if DB.is_suppressed(row["email"]): return self.json(409,{"error":"Адрес находится в suppression list."})
                if DB.conn.execute("SELECT 1 FROM send_logs WHERE lower(recipient)=lower(?) AND status IN ('SENT','SENDING','SEND_INTERRUPTED','FAILED','UNCERTAIN')", (row["email"],)).fetchone(): return self.json(409,{"error":"Для адреса уже есть send attempt; повтор заблокирован."})
                provider=SimulatedProvider()
                try:
                    provider.send(row["email"],row["subject"],row["body"])
                    DB.update_draft(row["id"],status="SIMULATED_SENT"); DB.log_send(row["id"],row["email"],provider.name,"SIMULATED_SENT"); DB.update_clinic_status(int(row["clinic_id"]),"SIMULATED_SENT")
                    return self.json(200,{"ok":True,"status":"SIMULATED_SENT","provider":provider.name})
                except Exception as exc:
                    print(f"[SEND] simulated draft={draft_id} failed: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
                    return self.json(500,{"error":"Симуляция отправки завершилась ошибкой."})
            finally:
                with SEND_LOCK: SENDING_DRAFTS.discard(draft_id)
        self.send_error(404)
    def log_message(self, *_): pass


def main():
    DB.recover_interrupted_jobs()
    port=int(os.environ.get("OUTREACH_PORT","8765")); print(f"Outreach Agent: http://127.0.0.1:{port}"); ThreadingHTTPServer(("127.0.0.1",port),Handler).serve_forever()

if __name__ == "__main__": main()
