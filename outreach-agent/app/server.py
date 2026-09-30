from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import uuid
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.analysis.lm_studio import LMStudioClient
from app.discovery.public_search import discover_candidates, mock_candidates
from app.discovery.datasets import curated_candidates
from app.discovery.engine import discover
from app.discovery.providers import DuckDuckGoProvider, WebProvider
from app.email.sender import SMTPProvider, SimulatedProvider
from app.extraction.public_page import inspect_site
from app.generation.email_draft import generate_draft, prepare_draft_formats
from app.autonomous import validate_analysis, EvidenceValidationError
from app.storage.db import Database
from app.qualification import qualify_lead
from app.verticals import get_vertical, build_search_query, sender_config, VERTICALS

DB = Database(os.environ.get("OUTREACH_DB_PATH", str(ROOT / "data" / "outreach.db")))
UI = ROOT / "app" / "ui" / "index.html"
DISCOVERY_RUNS: dict[str, dict] = {}
DISCOVERY_LOCK = threading.Lock()
ANALYSIS_LOCK = threading.Lock()
ANALYZING_LEADS: set[int] = set()


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


def _analyze_clinic(clinic_id: int) -> tuple[int, dict]:
    detail = DB.clinic_detail(clinic_id)
    if not detail: return 404, {"error": "Компания не найдена."}
    clinic, profile = detail["clinic"], detail["clinic"]["profile"]
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
    audit = profile.get("website_audit") or {}
    evidence = profile.get("evidence") or audit.get("evidence") or []
    if not audit and clinic.get("website"):
        audit = audit_website(clinic["website"])
        evidence = audit.get("evidence", [])
    payload = {"company": {k: clinic.get(k) for k in ("name", "city", "website")}, "sources": profile.get("sources", []), "contacts": contacts, "website_resolution": profile.get("website_resolution", {}), "website_audit": audit, "evidence": evidence}
    env = load_env()
    try:
        client = lm_client(env)
        analysis = validate_analysis(payload, client.chat_opportunity(payload))
    except EvidenceValidationError as exc:
        print(f"[ANALYSIS] lead={clinic_id} evidence validation failed: {exc}", flush=True)
        return 422, {"error": "Результат AI не прошёл проверку evidence. Повторите анализ позже."}
    except Exception as exc:
        print(f"[ANALYSIS] lead={clinic_id} failed: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
        return 503, {"error": "Не удалось завершить анализ. Проверьте, что LM Studio запущен и модель доступна."}

    lead = {**profile, "name": clinic.get("name"), "category": clinic.get("category"), "website": clinic.get("website"), "contacts": contacts, "evidence": payload["evidence"]}
    qualification = qualify_lead(lead, analysis, vertical)
    DB.add_analysis(clinic_id, client.model, payload, analysis)
    lead["qualification"], lead["vertical"] = qualification, vertical.key
    draft_warning = None
    if qualification["status"] == "QUALIFIED":
        existing = DB.active_draft(clinic_id)
        if existing:
            lead["draft_id"] = existing["id"]
            DB.update_clinic_status(clinic_id, "APPROVED" if existing["status"] == "APPROVED" else "DRAFTED", lead)
        else:
            DB.update_clinic_status(clinic_id, qualification["status"], lead)
            email_contact = next((item for item in contacts if item.get("email") and (item.get("source_url") or item.get("source"))), None)
            if not email_contact:
                draft_warning = "Публичный email с provenance не найден; draft не создавался."
            else:
                email = str(email_contact["email"]).strip().lower()
                if DB.is_suppressed(email):
                    draft_warning = "Адрес находится в suppression list; draft не создавался."
                    email_contact = None
                if email_contact:
                    contact_id = DB.add_contact(clinic_id, email, str(email_contact.get("source_url") or email_contact.get("source")), str(email_contact.get("type") or "PUBLIC_BUSINESS_EMAIL"))
                    if not contact_id:
                        draft_warning = "Для email найден конфликт контактов; draft оставлен на ручную проверку."
                    else:
                        try:
                            sender = config(env, vertical.key)
                            draft = generate_draft(client, lead, analysis, sender)
                            valid, reason = validate_dashboard_draft(draft, contacts, payload["evidence"], sender)
                            if not valid: raise ValueError(reason)
                            draft = build_email_formats(draft)
                            lead["draft_id"] = DB.add_draft(clinic_id, contact_id, draft)
                            DB.update_clinic_status(clinic_id, "DRAFTED", lead)
                        except Exception as exc:
                            print(f"[DRAFT] lead={clinic_id} generation failed: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
                            draft_warning = "Анализ сохранён, но локальная модель не создала draft. Повторите позже."
    else:
        DB.update_clinic_status(clinic_id, qualification["status"], lead)
    fresh = DB.clinic_detail(clinic_id)
    return 200, {"analysis": fresh["analysis"], "qualification": qualification, "draft": fresh["draft"], "draft_warning": draft_warning}


class Handler(BaseHTTPRequestHandler):
    def json(self, status: int, payload: dict):
        raw = json.dumps(payload, ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/dashboard": return self.json(200, DB.dashboard())
        if path == "/api/verticals": return self.json(200, {"items": [{"key": item.key, "label": item.label} for item in VERTICALS.values()]})
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
        if path == "/api/queue": return self.json(200, {"items": DB.list_queue()})
        if path == "/api/clinics": return self.json(200, {"items": DB.list_clinics()})
        if path == "/api/contacts": return self.json(200, {"items": DB.list_contacts()})
        if path == "/api/approved": return self.json(200, {"items": DB.list_queue("APPROVED")})
        if path == "/api/sent": return self.json(200, {"items": DB.list_send_logs()})
        if path == "/api/suppression": return self.json(200, {"items": DB.list_suppression()})
        if path == "/api/activity":
            items = DB.recent_activity()
            with DISCOVERY_LOCK:
                for run in DISCOVERY_RUNS.values():
                    items.extend({"type": f"DISCOVERY_{event.get('type', 'EVENT')}", "clinic_name": event.get("company") or event.get("provider") or "Discovery", "message": event.get("message") or event.get("error") or event.get("type"), "timestamp": ""} for event in run.get("activity", []))
            return self.json(200, {"items": items[:60]})
        if path == "/api/settings":
            env = load_env(); client = LMStudioClient(env.get("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1"), "qwen/qwen3-vl-8b")
            current_config = config(env)
            return self.json(200, {"lm_studio_url": client.base_url, "lm_studio_model": client.model, "smtp_enabled": False, "smtp_configured": False, "provider": "simulated", "smtp_host": "", "smtp_port": "", "sender_email": env.get("SENDER_EMAIL", ""), "real_sends": 0, **current_config})
        if path == "/api/health":
            env=load_env()
            try:
                client=lm_client(env)
                return self.json(200, {"lm_studio": True, "models": client.health()})
            except Exception as exc:
                print(f"[HEALTH] LM Studio unavailable: {type(exc).__name__}: {str(exc)[:240]}", flush=True)
                return self.json(200, {"lm_studio": False, "error": "LM Studio недоступен или настроенная модель не загружена."})
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
                target = max(1, min(100, int(data.get("target_count", 10))))
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
                target = max(1, min(100, int(data.get("target_count", 10))))
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
                    return self.json(409, {"error": "Анализ этой компании уже выполняется."})
                ANALYZING_LEADS.add(clinic_id)
            try:
                status, result = _analyze_clinic(clinic_id)
                return self.json(status, result)
            finally:
                with ANALYSIS_LOCK: ANALYZING_LEADS.discard(clinic_id)
        if path=="/api/pipeline": return self.json(200,run_pipeline(data.get("candidate") or mock_candidates()[0], bool(data.get("mock"))))
        if path=="/api/run-real":
            try: vertical = get_vertical(str(data.get("vertical", "dental")))
            except ValueError: return self.json(400, {"error": "Неизвестная вертикаль."})
            records = curated_candidates(vertical.key)
            if not records: return self.json(400, {"error": f"Для vertical {vertical.key} нет curated records."})
            candidates = [{**c, "category": vertical.category, "vertical": vertical.key, "precollected": True, "profile": c} for c in records[:int(data.get("target_count", 10))]]
            results = [run_pipeline(c, False) for c in candidates]
            return self.json(200, {"status": "REAL_DISCOVERY_COMPLETE", "count": len(results), "drafts": sum(1 for r in results if r.get("draft_id")), "results": results})
        if path=="/api/drafts/update": DB.update_draft(int(data["id"]),subject=data.get("subject",""),body=data.get("body","")); return self.json(200,{"ok":True})
        if path=="/api/drafts/status":
            status = data["status"]
            if status not in {"DRAFTED", "APPROVED", "SKIPPED", "DO_NOT_CONTACT"}: return self.json(400, {"error": "Недопустимый статус draft."})
            row = DB.get_draft(int(data["id"]))
            if not row or row["status"] in {"SENT", "DO_NOT_CONTACT"}: return self.json(409, {"error": "Этот draft больше нельзя approve/reopen."})
            if row["status"] == status: return self.json(200,{"ok":True})
            DB.update_draft(int(data["id"]),status=status)
            if status in {"DRAFTED", "APPROVED"}: DB.update_clinic_status(int(row["clinic_id"]), status)
            return self.json(200,{"ok":True})
        if path=="/api/suppress": DB.suppress(data["email"],data.get("reason","manual")); return self.json(200,{"ok":True})
        if path=="/api/suppression/remove": DB.remove_suppression(data["email"]); return self.json(200,{"ok":True})
        if path=="/api/send":
            row=DB.get_draft(int(data["id"]));
            if not row or row["status"]!="APPROVED": return self.json(409,{"error":"Отправка разрешена только для явно APPROVED draft."})
            if DB.is_suppressed(row["email"]): return self.json(409,{"error":"Адрес находится в suppression list."})
            if not data.get("simulate", True): return self.json(403, {"error": "SMTP отключён; реальные отправки недоступны в этой версии."})
            provider=SimulatedProvider()
            try: provider.send(row["email"],row["subject"],row["body"]); DB.update_draft(row["id"],status="SENT"); DB.log_send(row["id"],row["email"],provider.name,"SENT"); return self.json(200,{"ok":True,"provider":provider.name})
            except Exception as exc: DB.update_draft(row["id"],status="FAILED"); DB.log_send(row["id"],row["email"],provider.name,"FAILED",str(exc)); return self.json(500,{"error":str(exc)})
        self.send_error(404)
    def log_message(self, *_): pass


def main():
    port=int(os.environ.get("OUTREACH_PORT","8765")); print(f"Outreach Agent: http://127.0.0.1:{port}"); ThreadingHTTPServer(("127.0.0.1",port),Handler).serve_forever()

if __name__ == "__main__": main()
