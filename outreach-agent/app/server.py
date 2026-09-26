from __future__ import annotations

import json
import os
import re
import sys
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.analysis.lm_studio import LMStudioClient
from app.discovery.public_search import discover_candidates, mock_candidates
from app.discovery.real_candidates import REAL_ASTANA_CANDIDATES
from app.discovery.engine import discover
from app.discovery.providers import DuckDuckGoProvider
from app.email.sender import SMTPProvider, SimulatedProvider
from app.extraction.public_page import inspect_site
from app.generation.email_draft import generate_draft
from app.autonomous import validate_analysis
from app.storage.db import Database

DB = Database(ROOT / "data" / "outreach.db")
UI = ROOT / "app" / "ui" / "index.html"


def load_env() -> dict[str, str]:
    env = dict(os.environ)
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1); env.setdefault(key.strip(), value.strip())
    return env


def config(env: dict[str, str]) -> dict[str, str]:
    return {"demo_url": "https://yernaribadulla.github.io/Dentist_rus_commercial/", "sender_name": env.get("SENDER_NAME", "Yernar"), "sender_brand": env.get("SENDER_BRAND", "DENTARA"), "sender_contact": env.get("SENDER_CONTACT", env.get("SMTP_FROM", "yernaribadulla@gmail.com"))}


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
    body = draft.get("body", "")
    safe = escape(body).replace("\n", "<br>")
    draft["plain_text_body"] = body
    draft["html_body"] = f'<div style="font:15px Arial,sans-serif;line-height:1.6;color:#26342f">{safe.replace("AI-ассистент", "<abbr title=\"Отвечает на типовые вопросы, принимает обращения и помогает с записью, переносом и отменой визитов.\">AI-ассистент</abbr>")}</div>'
    return draft


DEMO_URL = "https://yernaribadulla.github.io/Dentist_rus_commercial/"


def validate_dashboard_draft(draft: dict, contacts: list[dict], evidence: list[dict]) -> tuple[bool, str]:
    subject, body = str(draft.get("subject", "")), str(draft.get("body", ""))
    if not subject.strip() or not body.strip(): return False, "subject and body are required"
    if DEMO_URL not in body: return False, "mandatory demo URL is missing"
    allowed_emails = {str(c.get("email", "")).lower() for c in contacts if c.get("email")}
    for email in re.findall(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", body):
        if email.lower() not in allowed_emails: return False, f"unsupported email in draft: {email}"
    urls = re.findall(r"https?://[^\s)<>]+", body)
    if any(url.rstrip(".,") not in {DEMO_URL} and url.rstrip(".,") not in {str(e.get("source", "")).rstrip(".,") for e in evidence} for url in urls):
        return False, "unsupported URL in draft"
    if not evidence: return False, "evidence is required"
    return True, "validated"


def run_pipeline(candidate: dict, use_mock: bool = False) -> dict:
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
    env = load_env(); client = lm_client(env)
    try:
        if use_mock:
            analysis = {"clinic_summary": "Фиктивная клиника для локального теста.", "observations": ["На публичной demo-странице указан общий business email.", "Публичный контактный путь ведёт на страницу контактов."], "potential_opportunities": ["Проверить путь записи на консультацию."], "personalization_points": ["Упомянуть спокойный digital patient experience."], "recommended_angle": "Показать DENTARA как пример премиального цифрового опыта.", "confidence": 0.91}
        else:
            payload = {"company": {k: candidate.get(k) for k in ("name", "city", "address", "website")}, "sources": candidate.get("sources", []), "contacts": contacts, "website_resolution": candidate.get("website_resolution", {"status": "WEBSITE_CANDIDATE", "evidence": [candidate.get("website")] }), "website_audit": candidate.get("website_audit", {}), "evidence": candidate.get("evidence", [])}
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
    DB.add_analysis(clinic_id, client.model, snapshot, analysis)
    if analysis["confidence"] == 0: return {"clinic_id": clinic_id, "status": "ANALYSIS_FAILED", "contacts": contacts, "analysis": analysis}
    try:
        if use_mock:
            draft = {"subject": "Идея для цифрового опыта Orda Smile", "body": "Здравствуйте!\n\nПосмотрел публичную demo-информацию Orda Smile и заметил возможность сделать путь пациента ещё более понятным — от первого контакта до записи на консультацию.\n\nЯ собрал DENTARA — пример премиального dental digital experience: " + (config(env).get("demo_url") or "demo-ссылка будет добавлена в настройках") + "\n\nЕсли тема неактуальна, просто ответьте на это письмо — я больше не буду вас беспокоить.", "rationale": "Mock-персонализация основана только на публичных demo-данных.", "source_observations": analysis["observations"], "confidence": analysis["confidence"]}
        else:
            draft = generate_draft(client, candidate, analysis, config(env))
    except Exception:
        # Keep the review queue useful if the second local generation call fails:
        # the analysis call still came from LM Studio, and this conservative
        # fallback only uses collected candidate facts.
        observation = (analysis.get("observations") or ["В открытых источниках собрана базовая информация о клинике."])[0]
        draft = {"subject": f"Идея для цифрового опыта {candidate.get('name', 'клиники')}", "body": f"Здравствуйте!\n\nЯ изучил открытые данные о {candidate.get('name', 'вашей клинике')}. {observation}\n\nЯ разрабатываю digital-решения для стоматологий: понятные сайты, удобный путь пациента и AI-ассистенты для типовых обращений. Пример DENTARA: {config(env).get('demo_url') or 'ссылка на demo будет добавлена в настройках'}.\n\nЕсли тема актуальна, могу коротко показать, как такой подход можно адаптировать под вашу клинику. Если вы не хотите получать подобные сообщения, просто ответьте — я больше не буду вас беспокоить.", "rationale": "Локальный анализ LM Studio не завершил второй шаг генерации; fallback использует только evidence из анализа.", "source_observations": analysis.get("observations", []), "confidence": analysis["confidence"]}
    valid, validation_reason = validate_dashboard_draft(draft, contacts, candidate.get("evidence", []) or [{"source": candidate.get("website", "")}])
    if not valid:
        return {"clinic_id": clinic_id, "status": "DRAFT_VALIDATION_FAILED", "reason": validation_reason, "contacts": contacts, "analysis": analysis}
    draft = build_email_formats(draft)
    draft_id = DB.add_draft(clinic_id, contact_id, draft)
    return {"clinic_id": clinic_id, "draft_id": draft_id, "status": "DRAFTED", "contacts": contacts, "analysis": analysis}


class Handler(BaseHTTPRequestHandler):
    def json(self, status: int, payload: dict):
        raw = json.dumps(payload, ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/dashboard": return self.json(200, DB.dashboard())
        if path == "/api/queue": return self.json(200, {"items": DB.list_queue()})
        if path == "/api/clinics": return self.json(200, {"items": DB.list_clinics()})
        if path == "/api/contacts": return self.json(200, {"items": DB.list_contacts()})
        if path == "/api/approved": return self.json(200, {"items": DB.list_queue("APPROVED")})
        if path == "/api/sent": return self.json(200, {"items": DB.list_send_logs()})
        if path == "/api/suppression": return self.json(200, {"items": DB.list_suppression()})
        if path == "/api/activity": return self.json(200, {"items": DB.recent_activity()})
        if path == "/api/settings":
            env = load_env(); client = LMStudioClient(env.get("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1"), "qwen/qwen3-vl-8b")
            current_config = config(env)
            smtp_ready = all(env.get(k) for k in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM"))
            return self.json(200, {"lm_studio_url": client.base_url, "lm_studio_model": client.model, "smtp_enabled": env.get("SMTP_ENABLED", "false").lower() == "true", "smtp_configured": smtp_ready, "provider": "smtp" if env.get("SMTP_ENABLED", "false").lower() == "true" else "simulated", "smtp_host": env.get("SMTP_HOST", "smtp.gmail.com"), "smtp_port": env.get("SMTP_PORT", "465"), "sender_email": env.get("SMTP_USERNAME", "yernaribadulla@gmail.com"), **current_config})
        if path == "/api/health":
            env=load_env()
            try:
                client=lm_client(env)
                return self.json(200, {"lm_studio": True, "models": client.health()})
            except Exception as exc: return self.json(200, {"lm_studio": False, "error": str(exc)[:180]})
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
        if path=="/api/discover":
            if data.get("mock"):
                candidates = mock_candidates()
            elif data.get("real") or data.get("city", "").lower() in {"астана", "astana"}:
                live = discover([DuckDuckGoProvider()], "dental", f"стоматология {data.get('city', 'Астана')}", int(data.get("target_count", 10)), audit_websites=True)
                candidates = live.candidates
            else:
                candidates=discover_candidates(data.get("city",""),data.get("country",""),data.get("category",""),data.get("keywords",""))
            return self.json(200,{"candidates":candidates,"count":len(candidates),"sources":["duckduckgo"] if not data.get("mock") else ["mock"]})
        if path=="/api/pipeline": return self.json(200,run_pipeline(data.get("candidate") or mock_candidates()[0], bool(data.get("mock"))))
        if path=="/api/run-real":
            candidates = [{**c, "precollected": True, "profile": c} for c in REAL_ASTANA_CANDIDATES[:int(data.get("target_count", 10))]]
            results = [run_pipeline(c, False) for c in candidates]
            return self.json(200, {"status": "REAL_DISCOVERY_COMPLETE", "count": len(results), "drafts": sum(1 for r in results if r.get("draft_id")), "results": results})
        if path=="/api/drafts/update": DB.update_draft(int(data["id"]),subject=data.get("subject",""),body=data.get("body","")); return self.json(200,{"ok":True})
        if path=="/api/drafts/status":
            status = data["status"]
            if status not in {"DRAFTED", "APPROVED", "SKIPPED", "DO_NOT_CONTACT"}: return self.json(400, {"error": "Недопустимый статус draft."})
            row = DB.get_draft(int(data["id"]))
            if not row or row["status"] in {"SENT", "DO_NOT_CONTACT"}: return self.json(409, {"error": "Этот draft больше нельзя approve/reopen."})
            DB.update_draft(int(data["id"]),status=status); return self.json(200,{"ok":True})
        if path=="/api/suppress": DB.suppress(data["email"],data.get("reason","manual")); return self.json(200,{"ok":True})
        if path=="/api/suppression/remove": DB.remove_suppression(data["email"]); return self.json(200,{"ok":True})
        if path=="/api/send":
            row=DB.get_draft(int(data["id"]));
            if not row or row["status"]!="APPROVED": return self.json(409,{"error":"Отправка разрешена только для явно APPROVED draft."})
            if DB.is_suppressed(row["email"]): return self.json(409,{"error":"Адрес находится в suppression list."})
            provider=SimulatedProvider() if data.get("simulate",True) else SMTPProvider(load_env())
            try: provider.send(row["email"],row["subject"],row["body"]); DB.update_draft(row["id"],status="SENT"); DB.log_send(row["id"],row["email"],provider.name,"SENT"); return self.json(200,{"ok":True,"provider":provider.name})
            except Exception as exc: DB.update_draft(row["id"],status="FAILED"); DB.log_send(row["id"],row["email"],provider.name,"FAILED",str(exc)); return self.json(500,{"error":str(exc)})
        self.send_error(404)
    def log_message(self, *_): pass


def main():
    port=int(os.environ.get("OUTREACH_PORT","8765")); print(f"Outreach Agent: http://127.0.0.1:{port}"); ThreadingHTTPServer(("127.0.0.1",port),Handler).serve_forever()

if __name__ == "__main__": main()
