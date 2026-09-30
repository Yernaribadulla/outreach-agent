from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.suppression_path = self.path.parent / "suppression.json"
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS clinics (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, website TEXT,
          city TEXT, country TEXT, category TEXT, phone TEXT, contact_page TEXT,
          description TEXT, source_url TEXT, collected_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DISCOVERED', profile_json TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS contacts (
          id INTEGER PRIMARY KEY AUTOINCREMENT, clinic_id INTEGER NOT NULL REFERENCES clinics(id),
          email TEXT NOT NULL UNIQUE, source_url TEXT NOT NULL, kind TEXT DEFAULT 'business', created_at TEXT NOT NULL,
          contact_type TEXT DEFAULT 'PUBLIC_BUSINESS_EMAIL', source_name TEXT DEFAULT '', confidence TEXT DEFAULT 'confirmed_by_source', is_public INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS analyses (
          id INTEGER PRIMARY KEY AUTOINCREMENT, clinic_id INTEGER NOT NULL REFERENCES clinics(id),
          model TEXT NOT NULL, input_snapshot TEXT NOT NULL, result_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS drafts (
          id INTEGER PRIMARY KEY AUTOINCREMENT, clinic_id INTEGER NOT NULL REFERENCES clinics(id),
          contact_id INTEGER NOT NULL REFERENCES contacts(id), subject TEXT NOT NULL, body TEXT NOT NULL,
          rationale TEXT NOT NULL, source_observations TEXT NOT NULL, confidence REAL NOT NULL,
          status TEXT NOT NULL DEFAULT 'DRAFTED', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          html_body TEXT DEFAULT '', plain_text_body TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS send_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, draft_id INTEGER, recipient TEXT NOT NULL,
          provider TEXT NOT NULL, status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS suppression_list (
          email TEXT PRIMARY KEY, reason TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS discovery_runs (
          run_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, vertical TEXT NOT NULL,
          source TEXT NOT NULL, input_file TEXT NOT NULL, received INTEGER NOT NULL,
          new_count INTEGER NOT NULL, duplicate_count INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS batch_jobs (
          batch_id TEXT PRIMARY KEY, vertical TEXT NOT NULL, requested_count INTEGER NOT NULL,
          queued_count INTEGER NOT NULL DEFAULT 0, processed_count INTEGER NOT NULL DEFAULT 0,
          analyzed_count INTEGER NOT NULL DEFAULT 0, qualified_count INTEGER NOT NULL DEFAULT 0,
          letters_generated INTEGER NOT NULL DEFAULT 0, needs_review INTEGER NOT NULL DEFAULT 0,
          failed INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, started_at TEXT NOT NULL,
          finished_at TEXT, current_company TEXT, current_stage TEXT, error_details TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS batch_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT NOT NULL REFERENCES batch_jobs(batch_id),
          clinic_id INTEGER NOT NULL REFERENCES clinics(id), company_name TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'PENDING', stage TEXT, error TEXT,
          started_at TEXT, finished_at TEXT, UNIQUE(batch_id, clinic_id)
        );
        CREATE TABLE IF NOT EXISTS send_jobs (
          send_id TEXT PRIMARY KEY, vertical TEXT NOT NULL, mode TEXT NOT NULL,
          requested_count INTEGER NOT NULL, planned_count INTEGER NOT NULL DEFAULT 0,
          sent_count INTEGER NOT NULL DEFAULT 0, simulated_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
          skipped_count INTEGER NOT NULL DEFAULT 0, min_delay_seconds INTEGER NOT NULL,
          max_delay_seconds INTEGER NOT NULL, max_per_batch INTEGER NOT NULL,
          daily_limit INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
          confirmed_at TEXT, started_at TEXT, finished_at TEXT, current_company TEXT,
          current_stage TEXT, error_details TEXT NOT NULL DEFAULT '[]'
        );
        CREATE TABLE IF NOT EXISTS send_items (
          id INTEGER PRIMARY KEY AUTOINCREMENT, send_id TEXT NOT NULL REFERENCES send_jobs(send_id),
          draft_id INTEGER NOT NULL REFERENCES drafts(id), clinic_id INTEGER NOT NULL REFERENCES clinics(id),
          company_name TEXT NOT NULL, recipient TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING',
          reason TEXT, started_at TEXT, finished_at TEXT, send_log_id INTEGER, UNIQUE(send_id, draft_id)
        );
        CREATE TABLE IF NOT EXISTS job_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, job_type TEXT NOT NULL, job_id TEXT NOT NULL,
          clinic_id INTEGER, draft_id INTEGER, company_name TEXT NOT NULL DEFAULT '',
          event TEXT NOT NULL, message TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS provider_cooldowns (
          provider TEXT PRIMARY KEY, cooldown_until TEXT NOT NULL, reason TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS one_running_analysis_batch ON batch_jobs(status) WHERE status='RUNNING';
        CREATE UNIQUE INDEX IF NOT EXISTS one_active_send_batch ON send_jobs(status) WHERE status IN ('RUNNING','SEND_INTERRUPTED');
        """)
        self._ensure_column("clinics", "profile_json", "TEXT DEFAULT '{}'" )
        self._ensure_column("contacts", "contact_type", "TEXT DEFAULT 'PUBLIC_BUSINESS_EMAIL'")
        self._ensure_column("contacts", "source_name", "TEXT DEFAULT ''")
        self._ensure_column("contacts", "confidence", "TEXT DEFAULT 'confirmed_by_source'")
        self._ensure_column("contacts", "is_public", "INTEGER DEFAULT 1")
        self._ensure_column("drafts", "html_body", "TEXT DEFAULT ''")
        self._ensure_column("drafts", "plain_text_body", "TEXT DEFAULT ''")
        self._ensure_column("discovery_runs", "status", "TEXT DEFAULT 'DISCOVERED'")
        self._ensure_column("discovery_runs", "target", "INTEGER DEFAULT 0")
        self._ensure_column("discovery_runs", "discovered", "INTEGER DEFAULT 0")
        self._ensure_column("discovery_runs", "researched", "INTEGER DEFAULT 0")
        self._ensure_column("discovery_runs", "qualified", "INTEGER DEFAULT 0")
        self._ensure_column("discovery_runs", "started_at", "TEXT")
        self._ensure_column("discovery_runs", "updated_at", "TEXT")
        self._ensure_column("discovery_runs", "result_json", "TEXT DEFAULT '{}'")
        # Correct legacy status without rewriting the timestamp of already-SENT drafts.
        self.conn.execute("UPDATE drafts SET status='SENT' WHERE status!='SENT' AND id IN (SELECT draft_id FROM send_logs WHERE status='SENT' AND draft_id IS NOT NULL)")
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        existing = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing: self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self) -> None:
        self.conn.close()

    def add_clinic(self, clinic: dict[str, Any]) -> int:
        existing = self.conn.execute("SELECT id,profile_json FROM clinics WHERE lower(name)=lower(?) AND lower(coalesce(city,''))=lower(coalesce(?,'')) LIMIT 1", (clinic.get("name"), clinic.get("city"))).fetchone()
        if existing:
            try: merged_profile = json.loads(existing["profile_json"] or "{}")
            except json.JSONDecodeError: merged_profile = {}
            incoming_profile = dict(clinic.get("profile", clinic))
            for key, value in incoming_profile.items():
                if value is None or value == [] or value == {}:
                    continue
                if key in {"contacts", "evidence", "sources", "conflicts"} and isinstance(value, list):
                    prior = merged_profile.get(key) or []
                    seen = {json.dumps(item, sort_keys=True, ensure_ascii=False) for item in prior}
                    merged_profile[key] = prior + [item for item in value if json.dumps(item, sort_keys=True, ensure_ascii=False) not in seen]
                else:
                    merged_profile[key] = value
            self.conn.execute("""UPDATE clinics SET website=COALESCE(?,website), country=COALESCE(?,country), category=COALESCE(?,category), phone=COALESCE(?,phone), contact_page=COALESCE(?,contact_page), description=COALESCE(?,description), source_url=COALESCE(?,source_url), profile_json=?, collected_at=? WHERE id=?""", (clinic.get("website"), clinic.get("country"), clinic.get("category"), clinic.get("phone"), clinic.get("contact_page"), clinic.get("description"), clinic.get("source_url"), json.dumps(merged_profile, ensure_ascii=False), utc_now(), int(existing[0])))
            self.conn.commit()
            return int(existing[0])
        cur = self.conn.execute("""INSERT INTO clinics(name,website,city,country,category,phone,contact_page,description,source_url,collected_at,profile_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (clinic.get("name"), clinic.get("website"), clinic.get("city"), clinic.get("country"), clinic.get("category"), clinic.get("phone"), clinic.get("contact_page"), clinic.get("description"), clinic.get("source_url"), utc_now(), json.dumps(clinic.get("profile", clinic), ensure_ascii=False)))
        self.conn.commit(); return int(cur.lastrowid)

    def list_discovery_runs(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM discovery_runs ORDER BY timestamp DESC").fetchall()]

    def get_discovery_run(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM discovery_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row: return None
        result = dict(row)
        try: payload = json.loads(result.get("result_json") or "{}")
        except json.JSONDecodeError: payload = {}
        result.update(payload if isinstance(payload, dict) else {})
        result.setdefault("count", result.get("discovered", 0))
        result.setdefault("candidates", [])
        result.setdefault("activity", [])
        result.setdefault("source_status", {})
        return result

    def latest_discovery_run(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT run_id FROM discovery_runs WHERE input_file='dashboard' ORDER BY timestamp DESC LIMIT 1").fetchone()
        return self.get_discovery_run(str(row[0])) if row else None

    def save_discovery_result(self, run_id: str, result: dict[str, Any]) -> None:
        self.conn.execute("UPDATE discovery_runs SET status=?, discovered=?, result_json=?, updated_at=? WHERE run_id=?", (result.get("status", "SEARCHING_SOURCES"), int(result.get("count", 0)), json.dumps(result, ensure_ascii=False), utc_now(), run_id))
        self.conn.commit()

    def create_autonomous_run(self, run_id: str, vertical: str, target: int, discovered: int = 0) -> None:
        now = utc_now()
        self.conn.execute("INSERT INTO discovery_runs(run_id,timestamp,vertical,source,input_file,received,new_count,duplicate_count,status,target,discovered,researched,qualified,started_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (run_id, now, vertical, "openstreetmap", "autonomous", discovered, 0, 0, "DISCOVERED", target, discovered, 0, 0, now, now)); self.conn.commit()

    def create_discovery_run(self, run_id: str, vertical: str, target: int, source: str = "public_search") -> None:
        now = utc_now()
        self.conn.execute("INSERT INTO discovery_runs(run_id,timestamp,vertical,source,input_file,received,new_count,duplicate_count,status,target,discovered,researched,qualified,started_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (run_id, now, vertical, source, "dashboard", 0, 0, 0, "SEARCHING_SOURCES", target, 0, 0, 0, now, now))
        self.conn.commit()

    def update_autonomous_run(self, run_id: str, **fields: Any) -> None:
        allowed = {k: v for k, v in fields.items() if k in {"status", "discovered", "researched", "qualified"}}
        if not allowed: return
        allowed["updated_at"] = utc_now()
        values = list(allowed.values()) + [run_id]
        self.conn.execute(f"UPDATE discovery_runs SET {', '.join(k+'=?' for k in allowed)} WHERE run_id=?", values); self.conn.commit()

    def update_clinic_status(self, clinic_id: int, status: str, profile: dict[str, Any] | None = None) -> None:
        if profile is None:
            self.conn.execute("UPDATE clinics SET status=?, collected_at=? WHERE id=?", (status, utc_now(), clinic_id))
        else:
            self.conn.execute("UPDATE clinics SET status=?, profile_json=?, collected_at=? WHERE id=?", (status, json.dumps(profile, ensure_ascii=False), utc_now(), clinic_id))
        self.conn.commit()

    def add_contact(self, clinic_id: int, email: str, source_url: str, kind: str = "business") -> int | None:
        try:
            cur = self.conn.execute("INSERT INTO contacts(clinic_id,email,source_url,kind,created_at,contact_type,source_name,confidence,is_public) VALUES(?,?,?,?,?,?,?,?,?)", (clinic_id, email.lower(), source_url, kind, utc_now(), "PUBLIC_BUSINESS_EMAIL", kind, "confirmed_by_source", 1))
            self.conn.commit(); return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            row = self.conn.execute("SELECT id,clinic_id FROM contacts WHERE email=?", (email.lower(),)).fetchone()
            return int(row[0]) if row and int(row[1]) == clinic_id else None

    def active_draft(self, clinic_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM drafts WHERE clinic_id=? AND status IN ('DRAFTED','APPROVED') ORDER BY updated_at DESC,id DESC LIMIT 1", (clinic_id,)).fetchone()
        if not row: return None
        item = dict(row)
        try: item["source_observations"] = json.loads(item["source_observations"])
        except json.JSONDecodeError: item["source_observations"] = []
        return item

    def add_analysis(self, clinic_id: int, model: str, snapshot: dict[str, Any], result: dict[str, Any]) -> int:
        cur = self.conn.execute("INSERT INTO analyses(clinic_id,model,input_snapshot,result_json,created_at) VALUES(?,?,?,?,?)", (clinic_id, model, json.dumps(snapshot, ensure_ascii=False), json.dumps(result, ensure_ascii=False), utc_now()))
        self.conn.execute("UPDATE clinics SET status='ANALYZED' WHERE id=?", (clinic_id,)); self.conn.commit(); return int(cur.lastrowid)

    def add_draft(self, clinic_id: int, contact_id: int, draft: dict[str, Any], *, force: bool = False) -> int:
        existing = self.conn.execute("SELECT id FROM drafts WHERE clinic_id=? AND status IN ('DRAFTED','APPROVED') ORDER BY updated_at DESC LIMIT 1", (clinic_id,)).fetchone()
        if existing:
            if not force: return int(existing[0])
            self.conn.execute("UPDATE drafts SET status='SUPERSEDED',updated_at=? WHERE id=? AND status IN ('DRAFTED','APPROVED')", (utc_now(), int(existing[0])))
        cur = self.conn.execute("""INSERT INTO drafts(clinic_id,contact_id,subject,body,rationale,source_observations,confidence,status,created_at,updated_at,html_body,plain_text_body)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (clinic_id, contact_id, draft["subject"], draft["body"], draft["rationale"], json.dumps(draft.get("source_observations", []), ensure_ascii=False), float(draft.get("confidence", 0)), "DRAFTED", utc_now(), utc_now(), draft.get("html_body", ""), draft.get("plain_text_body", draft["body"])))
        self.conn.execute("UPDATE clinics SET status='LETTER_DONE' WHERE id=?", (clinic_id,)); self.conn.commit(); return int(cur.lastrowid)

    def list_queue(self, status: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE d.status=?" if status else "WHERE d.status IN ('DRAFTED','APPROVED')"
        args = (status,) if status else ()
        rows = self.conn.execute(f"""SELECT d.*, c.name clinic_name,c.website,c.city,c.country,c.source_url,c.description,c.phone,c.profile_json,ct.email,ct.source_url email_source,ct.contact_type,ct.source_name,ct.confidence contact_confidence
          FROM drafts d JOIN clinics c ON c.id=d.clinic_id JOIN contacts ct ON ct.id=d.contact_id {where} ORDER BY d.updated_at DESC""", args).fetchall()
        result = []
        for row in rows:
            item = dict(row); item["source_observations"] = json.loads(item["source_observations"]); result.append(item)
        return result

    def ready_to_send(self, vertical: str | None = None, limit: int = 10000) -> list[dict[str, Any]]:
        """Return only approved, public, unsuppressed, never-attempted business email drafts."""
        rows = self.conn.execute("""SELECT d.*,c.name clinic_name,c.category,c.profile_json,c.status clinic_status,
          ct.email,ct.source_url email_source,ct.contact_type,ct.is_public
          FROM drafts d JOIN clinics c ON c.id=d.clinic_id JOIN contacts ct ON ct.id=d.contact_id
          WHERE d.status='APPROVED' ORDER BY d.updated_at,d.id""").fetchall()
        suppressed = {str(row[0]).lower() for row in self.conn.execute("SELECT email FROM suppression_list")}
        attempted_recipients = {str(row[0]).lower() for row in self.conn.execute("SELECT recipient FROM send_logs WHERE status IN ('SENT','SENDING','SEND_INTERRUPTED','FAILED','UNCERTAIN')")}
        sent_drafts = {int(row[0]) for row in self.conn.execute("SELECT draft_id FROM send_logs WHERE status='SENT' AND draft_id IS NOT NULL")}
        result, seen_recipients, seen_clinics = [], set(), set()
        for raw in rows:
            item = dict(raw)
            profile = {}
            try: profile = json.loads(item.get("profile_json") or "{}")
            except (TypeError, json.JSONDecodeError): pass
            clinic_vertical = str(profile.get("vertical") or item.get("category") or "").lower()
            email = str(item.get("email") or "").strip().lower()
            clinic_id = int(item["clinic_id"])
            if vertical and clinic_vertical != vertical.lower(): continue
            if item.get("clinic_status") in {"DO_NOT_CONTACT", "SENT", "SEND_FAILED", "SEND_INTERRUPTED"}: continue
            if item["id"] in sent_drafts or email in suppressed or email in attempted_recipients: continue
            if email in seen_recipients or clinic_id in seen_clinics: continue
            if item.get("contact_type") != "PUBLIC_BUSINESS_EMAIL" or int(item.get("is_public") or 0) != 1: continue
            source = str(item.get("email_source") or "")
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email) or not source.startswith(("http://", "https://")): continue
            seen_recipients.add(email); seen_clinics.add(clinic_id)
            result.append(item)
            if len(result) >= limit: break
        return result

    def create_batch_job(self, batch_id: str, vertical: str, requested_count: int, clinics: list[dict[str, Any]]) -> None:
        now = utc_now()
        self.conn.execute("INSERT INTO batch_jobs(batch_id,vertical,requested_count,queued_count,status,started_at,current_stage) VALUES(?,?,?,?,?,?,?)", (batch_id, vertical, requested_count, len(clinics), "RUNNING", now, "QUEUED"))
        self.conn.executemany("INSERT INTO batch_items(batch_id,clinic_id,company_name,status) VALUES(?,?,?,'PENDING')", [(batch_id, int(row["id"]), str(row["name"])) for row in clinics])
        self.add_job_event("analysis", batch_id, "BATCH_STARTED", f"Batch started · {len(clinics)} clinic(s) queued")
        self.conn.commit()

    def analysis_candidates(self, vertical: str, limit: int) -> list[dict[str, Any]]:
        rows = self.conn.execute("""SELECT c.id,c.name,c.category,c.status,c.profile_json,
          EXISTS(SELECT 1 FROM analyses a WHERE a.clinic_id=c.id) has_analysis,
          EXISTS(SELECT 1 FROM drafts d WHERE d.clinic_id=c.id AND d.status IN ('DRAFTED','APPROVED','SENT','SENDING','SEND_INTERRUPTED')) has_draft
          FROM clinics c WHERE c.status NOT IN ('DO_NOT_CONTACT','SENT','DISQUALIFIED','SEND_FAILED','SEND_INTERRUPTED')
          ORDER BY c.collected_at,c.id""").fetchall()
        result = []
        for raw in rows:
            item = dict(raw)
            try: profile = json.loads(item.get("profile_json") or "{}")
            except (TypeError, json.JSONDecodeError): profile = {}
            clinic_vertical = str(profile.get("vertical") or item.get("category") or "").lower()
            if clinic_vertical != vertical.lower(): continue
            qualification = (profile.get("qualification") or {}).get("status")
            no_analysis_work = not item["has_analysis"] and item["status"] in {"DISCOVERED", "RESEARCHED", "ANALYZED"}
            cached_qualification_work = item["has_analysis"] and qualification in {None, "QUALIFIED"} and not item["has_draft"]
            if not (no_analysis_work or cached_qualification_work): continue
            result.append({"id": item["id"], "name": item["name"], "category": item["category"], "profile": profile})
            if len(result) >= limit: break
        return result

    def resume_batch_job(self, batch_id: str) -> bool:
        cur = self.conn.execute("UPDATE batch_jobs SET status='RUNNING',finished_at=NULL,current_stage='RESUMING' WHERE batch_id=? AND status='INTERRUPTED' AND EXISTS(SELECT 1 FROM batch_items WHERE batch_id=? AND status='PENDING')", (batch_id, batch_id))
        if cur.rowcount:
            self.add_job_event("analysis", batch_id, "BATCH_RESUMED", "Batch resumed by explicit user action")
        self.conn.commit()
        return cur.rowcount == 1

    def get_batch_job(self, batch_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM batch_jobs WHERE batch_id=?", (batch_id,)).fetchone()
        if not row: return None
        result = dict(row)
        try: result["error_details"] = json.loads(result["error_details"] or "[]")
        except json.JSONDecodeError: result["error_details"] = []
        result["items"] = [dict(item) for item in self.conn.execute("SELECT * FROM batch_items WHERE batch_id=? ORDER BY id", (batch_id,))]
        return result

    def latest_batch_job(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT batch_id FROM batch_jobs ORDER BY started_at DESC LIMIT 1").fetchone()
        return self.get_batch_job(str(row[0])) if row else None

    def update_batch_item(self, batch_id: str, clinic_id: int, **fields: Any) -> None:
        allowed = {key: value for key, value in fields.items() if key in {"status", "stage", "error", "started_at", "finished_at"}}
        if not allowed: return
        self.conn.execute(f"UPDATE batch_items SET {', '.join(key+'=?' for key in allowed)} WHERE batch_id=? AND clinic_id=?", [*allowed.values(), batch_id, clinic_id])
        if "stage" in allowed:
            row = self.conn.execute("SELECT company_name FROM batch_items WHERE batch_id=? AND clinic_id=?", (batch_id, clinic_id)).fetchone()
            self.conn.execute("UPDATE batch_jobs SET current_company=?,current_stage=? WHERE batch_id=?", (row[0] if row else None, allowed["stage"], batch_id))
        self._refresh_batch_counts(batch_id)
        self.conn.commit()

    def _refresh_batch_counts(self, batch_id: str) -> None:
        counts = dict(self.conn.execute("SELECT status,COUNT(*) FROM batch_items WHERE batch_id=? GROUP BY status", (batch_id,)).fetchall())
        processed = sum(counts.get(status, 0) for status in ("COMPLETED", "FAILED", "SKIPPED"))
        row = self.conn.execute("""SELECT COUNT(*) analyzed,
          SUM(CASE WHEN json_extract(c.profile_json,'$.qualification.status')='QUALIFIED' THEN 1 ELSE 0 END) qualified,
          SUM(CASE WHEN json_extract(c.profile_json,'$.qualification.status')='NEEDS_REVIEW' THEN 1 ELSE 0 END) needs_review
          FROM batch_items i JOIN clinics c ON c.id=i.clinic_id WHERE i.batch_id=? AND i.status IN ('COMPLETED','FAILED') AND EXISTS(SELECT 1 FROM analyses a WHERE a.clinic_id=c.id)""", (batch_id,)).fetchone()
        letters = self.conn.execute("SELECT COUNT(DISTINCT d.clinic_id) FROM batch_items i JOIN drafts d ON d.clinic_id=i.clinic_id WHERE i.batch_id=? AND i.status IN ('COMPLETED','FAILED') AND d.status IN ('DRAFTED','APPROVED','SENT')", (batch_id,)).fetchone()[0]
        failed = counts.get("FAILED", 0)
        self.conn.execute("UPDATE batch_jobs SET processed_count=?,analyzed_count=?,qualified_count=?,letters_generated=?,needs_review=?,failed=? WHERE batch_id=?", (processed, int(row["analyzed"] or 0), int(row["qualified"] or 0), letters, int(row["needs_review"] or 0), failed, batch_id))

    def finish_batch_job(self, batch_id: str, status: str = "COMPLETED") -> None:
        self._refresh_batch_counts(batch_id)
        self.conn.execute("UPDATE batch_jobs SET status=?,finished_at=?,current_company=NULL,current_stage=? WHERE batch_id=?", (status, utc_now(), status, batch_id))
        self.add_job_event("analysis", batch_id, f"BATCH_{status}", f"Batch {status.lower()}")
        self.conn.commit()

    def create_send_job(self, send_id: str, vertical: str, mode: str, requested_count: int, min_delay: int, max_delay: int, max_per_batch: int, daily_limit: int, items: list[dict[str, Any]]) -> None:
        now = utc_now()
        self.conn.execute("INSERT INTO send_jobs(send_id,vertical,mode,requested_count,planned_count,min_delay_seconds,max_delay_seconds,max_per_batch,daily_limit,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,'PREVIEW',?)", (send_id, vertical, mode, requested_count, len(items), min_delay, max_delay, max_per_batch, daily_limit, now))
        self.conn.executemany("INSERT INTO send_items(send_id,draft_id,clinic_id,company_name,recipient) VALUES(?,?,?,?,?)", [(send_id, int(row["id"]), int(row["clinic_id"]), str(row["clinic_name"]), str(row["email"])) for row in items])
        self.conn.commit()

    def get_send_job(self, send_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM send_jobs WHERE send_id=?", (send_id,)).fetchone()
        if not row: return None
        result = dict(row)
        try: result["error_details"] = json.loads(result["error_details"] or "[]")
        except json.JSONDecodeError: result["error_details"] = []
        result["items"] = [dict(item) for item in self.conn.execute("SELECT * FROM send_items WHERE send_id=? ORDER BY id", (send_id,))]
        return result

    def latest_send_job(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT send_id FROM send_jobs ORDER BY created_at DESC LIMIT 1").fetchone()
        return self.get_send_job(str(row[0])) if row else None

    def active_send_job(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT send_id FROM send_jobs WHERE status IN ('RUNNING','SEND_INTERRUPTED') ORDER BY created_at DESC LIMIT 1").fetchone()
        return self.get_send_job(str(row[0])) if row else None

    def provider_cooldown(self) -> dict[str, str] | None:
        row = self.conn.execute("SELECT cooldown_until,reason FROM provider_cooldowns WHERE provider='smtp'").fetchone()
        return dict(row) if row else None

    def set_provider_cooldown(self, cooldown_until: str, reason: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO provider_cooldowns(provider,cooldown_until,reason) VALUES('smtp',?,?)", (cooldown_until, reason))
        self.conn.commit()

    def confirm_send_job(self, send_id: str, status: str = "RUNNING") -> bool:
        now = utc_now()
        cur = self.conn.execute("UPDATE send_jobs SET status=?,confirmed_at=?,started_at=COALESCE(started_at,?),current_stage='SENDING' WHERE send_id=? AND status IN ('PREVIEW','SEND_INTERRUPTED')", (status, now, now, send_id))
        self.conn.commit()
        return cur.rowcount == 1

    def update_send_item(self, send_id: str, draft_id: int, **fields: Any) -> None:
        allowed = {key: value for key, value in fields.items() if key in {"status", "reason", "started_at", "finished_at"}}
        if not allowed: return
        self.conn.execute(f"UPDATE send_items SET {', '.join(key+'=?' for key in allowed)} WHERE send_id=? AND draft_id=?", [*allowed.values(), send_id, draft_id])
        self._refresh_send_counts(send_id)
        self.conn.commit()

    def reserve_send_item(self, send_id: str, draft_id: int) -> tuple[dict[str, Any] | None, str | None]:
        """Atomically reserve an approved draft before a provider can be called."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            job = self.conn.execute("SELECT mode,status FROM send_jobs WHERE send_id=?", (send_id,)).fetchone()
            item = self.conn.execute("""SELECT i.*,d.status draft_status,d.subject,d.body,d.html_body,c.status clinic_status,
              ct.contact_type,ct.is_public,ct.source_url
              FROM send_items i JOIN drafts d ON d.id=i.draft_id JOIN clinics c ON c.id=i.clinic_id
              JOIN contacts ct ON ct.id=d.contact_id WHERE i.send_id=? AND i.draft_id=?""", (send_id, draft_id)).fetchone()
            reason = None
            if not job or job["status"] != "RUNNING": reason = "send job is not running"
            elif not item or item["status"] != "PENDING": reason = "item is no longer pending"
            elif item["draft_status"] != "APPROVED": reason = "draft is not approved"
            elif item["clinic_status"] in {"DO_NOT_CONTACT", "SENT", "SEND_FAILED", "SEND_INTERRUPTED"}: reason = "clinic is not eligible for sending"
            elif item["contact_type"] != "PUBLIC_BUSINESS_EMAIL" or not int(item["is_public"] or 0): reason = "recipient is not a public business contact"
            elif not str(item["source_url"] or "").startswith(("http://", "https://")): reason = "public contact source is missing"
            elif self.is_suppressed(item["recipient"]): reason = "recipient is suppressed"
            elif self.conn.execute("SELECT 1 FROM send_logs WHERE lower(recipient)=lower(?) AND status IN ('SENT','SENDING','SEND_INTERRUPTED','FAILED','UNCERTAIN')", (item["recipient"],)).fetchone(): reason = "recipient has a prior or uncertain send attempt"
            elif self.conn.execute("SELECT 1 FROM send_logs WHERE draft_id=? AND status IN ('SENT','SENDING','SEND_INTERRUPTED','FAILED','UNCERTAIN')", (draft_id,)).fetchone(): reason = "draft has a prior or uncertain send attempt"
            if reason:
                self.conn.rollback()
                return None, reason
            provider = "smtp" if job["mode"] == "REAL_SMTP" else "simulated"
            now = utc_now()
            cursor = self.conn.execute("INSERT INTO send_logs(draft_id,recipient,provider,status,created_at) VALUES(?,?,?,?,?)", (draft_id, item["recipient"], provider, "SENDING", now))
            log_id = int(cursor.lastrowid)
            self.conn.execute("UPDATE drafts SET status='SENDING',updated_at=? WHERE id=? AND status='APPROVED'", (now, draft_id))
            self.conn.execute("UPDATE clinics SET status='SENDING' WHERE id=?", (item["clinic_id"],))
            self.conn.execute("UPDATE send_items SET status='SENDING',started_at=?,send_log_id=? WHERE send_id=? AND draft_id=?", (now, log_id, send_id, draft_id))
            self.conn.execute("UPDATE send_jobs SET current_company=?,current_stage='SENDING' WHERE send_id=?", (item["company_name"], send_id))
            self.conn.commit()
            return {**dict(item), "send_log_id": log_id, "provider": provider}, None
        except Exception:
            self.conn.rollback()
            raise

    def complete_send_attempt(self, send_id: str, draft_id: int, send_log_id: int, status: str, error: str | None = None) -> None:
        now = utc_now()
        item = self.conn.execute("SELECT clinic_id,company_name FROM send_items WHERE send_id=? AND draft_id=?", (send_id, draft_id)).fetchone()
        draft_status = {"SENT": "SENT", "SIMULATED_SENT": "SIMULATED_SENT", "FAILED": "SEND_FAILED", "UNCERTAIN": "SEND_INTERRUPTED"}.get(status, status)
        self.conn.execute("UPDATE send_logs SET status=?,error=? WHERE id=? AND status='SENDING'", (status, error, send_log_id))
        self.conn.execute("UPDATE drafts SET status=?,updated_at=? WHERE id=? AND status='SENDING'", (draft_status, now, draft_id))
        if item:
            self.conn.execute("UPDATE clinics SET status=? WHERE id=?", (draft_status, item["clinic_id"]))
        self.conn.execute("UPDATE send_items SET status=?,reason=?,finished_at=? WHERE send_id=? AND draft_id=?", (status, error, now, send_id, draft_id))
        self._refresh_send_counts(send_id)
        self.add_job_event("send", send_id, status, f"{item['company_name'] if item else 'Company'} — {status}" + (f" · {error}" if error else ""), int(item["clinic_id"]) if item else None, draft_id, str(item["company_name"]) if item else "")
        self.conn.commit()

    def _refresh_send_counts(self, send_id: str) -> None:
        counts = dict(self.conn.execute("SELECT status,COUNT(*) FROM send_items WHERE send_id=? GROUP BY status", (send_id,)).fetchall())
        self.conn.execute("UPDATE send_jobs SET sent_count=?,simulated_count=?,failed_count=?,skipped_count=? WHERE send_id=?", (counts.get("SENT", 0), counts.get("SIMULATED_SENT", 0), counts.get("FAILED", 0) + counts.get("UNCERTAIN", 0), counts.get("SKIPPED", 0), send_id))

    def finish_send_job(self, send_id: str, status: str, error: str | None = None) -> None:
        job = self.get_send_job(send_id)
        errors = list(job.get("error_details", [])) if job else []
        if error: errors.append(error)
        self._refresh_send_counts(send_id)
        self.conn.execute("UPDATE send_jobs SET status=?,finished_at=?,current_company=NULL,current_stage=?,error_details=? WHERE send_id=?", (status, utc_now(), status, json.dumps(errors, ensure_ascii=False), send_id))
        self.add_job_event("send", send_id, f"SEND_{status}", error or f"Send batch {status.lower()}")
        self.conn.commit()

    def add_job_event(self, job_type: str, job_id: str, event: str, message: str, clinic_id: int | None = None, draft_id: int | None = None, company_name: str = "") -> None:
        self.conn.execute("INSERT INTO job_events(job_type,job_id,clinic_id,draft_id,company_name,event,message,created_at) VALUES(?,?,?,?,?,?,?,?)", (job_type, job_id, clinic_id, draft_id, company_name, event, message, utc_now()))
        self.conn.commit()

    def job_activity(self, limit: int = 100) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM job_events ORDER BY id DESC LIMIT ?", (limit,))]

    def recover_interrupted_jobs(self) -> None:
        now = utc_now()
        for clinic in self.conn.execute("SELECT id,profile_json,EXISTS(SELECT 1 FROM analyses a WHERE a.clinic_id=clinics.id) has_analysis FROM clinics WHERE status='ANALYZING'").fetchall():
            try: qualification = json.loads(clinic["profile_json"] or "{}").get("qualification", {}).get("status")
            except (json.JSONDecodeError, AttributeError): qualification = None
            recovered = qualification if qualification in {"QUALIFIED", "NEEDS_REVIEW", "DISQUALIFIED"} else ("ANALYZED" if clinic["has_analysis"] else "DISCOVERED")
            self.conn.execute("UPDATE clinics SET status=? WHERE id=? AND status='ANALYZING'", (recovered, clinic["id"]))
        running_batches = [row[0] for row in self.conn.execute("SELECT batch_id FROM batch_jobs WHERE status='RUNNING'")]
        for batch_id in running_batches:
            self.conn.execute("UPDATE batch_items SET status='PENDING',stage='INTERRUPTED' WHERE batch_id=? AND status IN ('ANALYZING','GENERATING_LETTER')", (batch_id,))
            self.conn.execute("UPDATE batch_jobs SET status='INTERRUPTED',finished_at=?,current_company=NULL,current_stage='INTERRUPTED' WHERE batch_id=?", (now, batch_id))
            self.add_job_event("analysis", batch_id, "BATCH_INTERRUPTED", "Batch interrupted by server restart")
        running_sends = [row[0] for row in self.conn.execute("SELECT send_id FROM send_jobs WHERE status='RUNNING'")]
        for send_id in running_sends:
            current = self.conn.execute("SELECT draft_id,clinic_id,company_name FROM send_items WHERE send_id=? AND status='SENDING'", (send_id,)).fetchone()
            if current:
                draft_id, clinic_id, company_name = current
                self.conn.execute("UPDATE send_items SET status='UNCERTAIN',reason='Server stopped while provider outcome was unknown.',finished_at=? WHERE send_id=? AND draft_id=?", (now, send_id, draft_id))
                self.conn.execute("UPDATE send_logs SET status='SEND_INTERRUPTED',error='Server restarted during the provider attempt.' WHERE draft_id=? AND status='SENDING'", (draft_id,))
                self.conn.execute("UPDATE drafts SET status='SEND_INTERRUPTED',updated_at=? WHERE id=? AND status='SENDING'", (now, draft_id))
                self.conn.execute("UPDATE clinics SET status='SEND_INTERRUPTED' WHERE id=?", (clinic_id,))
                self.add_job_event("send", send_id, "SEND_INTERRUPTED", "Current provider attempt is uncertain and will not be retried automatically.", int(clinic_id), int(draft_id), str(company_name))
            self.conn.execute("UPDATE send_jobs SET status='SEND_INTERRUPTED',finished_at=?,current_company=NULL,current_stage='INTERRUPTED' WHERE send_id=?", (now, send_id))
            self.add_job_event("send", send_id, "SEND_INTERRUPTED", "Send batch interrupted by server restart; explicit resume is required.")
        self.conn.commit()

    def list_clinics(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("""SELECT c.*, COUNT(DISTINCT ct.id) contact_count,
          GROUP_CONCAT(DISTINCT ct.email) emails, COUNT(DISTINCT a.id) analysis_count
          FROM clinics c LEFT JOIN contacts ct ON ct.clinic_id=c.id LEFT JOIN analyses a ON a.clinic_id=c.id
          GROUP BY c.id ORDER BY c.collected_at DESC""").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            try:
                profile = json.loads(item.get("profile_json") or "{}")
                qualification = profile.get("qualification") or {}
                item.update({"website_status": profile.get("website_status", "WEBSITE_UNCERTAIN"), "website_quality_observations": profile.get("website_quality_observations", []), "services": profile.get("services", []), "source_urls": profile.get("source_urls", []), "qualification_status": qualification.get("status", "NOT_ASSESSED"), "qualification": qualification, "vertical": item.get("category") or "unknown"})
            except json.JSONDecodeError: pass
            draft_state = self.conn.execute("SELECT status FROM drafts WHERE clinic_id=? ORDER BY id DESC LIMIT 1", (item["id"],)).fetchone()
            if draft_state and draft_state[0] == "SENT": item["workflow_status"] = "SENT"
            elif draft_state and draft_state[0] == "APPROVED": item["workflow_status"] = "READY_TO_SEND" if any(row["clinic_id"] == item["id"] for row in self.ready_to_send()) else "APPROVED"
            elif draft_state and draft_state[0] in {"DRAFTED", "SIMULATED_SENT", "SENDING", "SEND_INTERRUPTED", "SEND_FAILED"}: item["workflow_status"] = {"DRAFTED": "LETTER_DONE"}.get(draft_state[0], draft_state[0])
            else: item["workflow_status"] = item.get("status", "DISCOVERED")
            result.append(item)
        return result

    def clinic_detail(self, clinic_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM clinics WHERE id=?", (clinic_id,)).fetchone()
        if not row: return None
        clinic = dict(row)
        try: clinic["profile"] = json.loads(clinic.pop("profile_json") or "{}")
        except json.JSONDecodeError: clinic["profile"] = {}
        analysis_row = self.conn.execute("SELECT result_json,input_snapshot,model,created_at FROM analyses WHERE clinic_id=? ORDER BY created_at DESC,id DESC LIMIT 1", (clinic_id,)).fetchone()
        analysis = None
        analysis_snapshot = {}
        if analysis_row:
            try: analysis = json.loads(analysis_row["result_json"])
            except json.JSONDecodeError: analysis = None
            try: analysis_snapshot = json.loads(analysis_row["input_snapshot"] or "{}")
            except json.JSONDecodeError: analysis_snapshot = {}
            if analysis is not None: analysis.update({"model": analysis_row["model"], "created_at": analysis_row["created_at"]})
        draft_row = self.conn.execute("SELECT * FROM drafts WHERE clinic_id=? AND status NOT IN ('FAILED','SKIPPED','SUPERSEDED') ORDER BY updated_at DESC,id DESC LIMIT 1", (clinic_id,)).fetchone()
        draft = dict(draft_row) if draft_row else None
        if draft:
            try: draft["source_observations"] = json.loads(draft["source_observations"])
            except json.JSONDecodeError: draft["source_observations"] = []
        history = []
        for record in self.conn.execute("SELECT id,model,created_at,result_json FROM analyses WHERE clinic_id=? ORDER BY created_at DESC,id DESC LIMIT 20", (clinic_id,)):
            try: summary = json.loads(record["result_json"] or "{}")
            except json.JSONDecodeError: summary = {}
            history.append({"id": record["id"], "model": record["model"], "created_at": record["created_at"], "recommended_angle": summary.get("recommended_angle"), "qualification_status": (clinic.get("profile") or {}).get("qualification", {}).get("status")})
        contacts = [dict(x) for x in self.conn.execute("SELECT * FROM contacts WHERE clinic_id=? ORDER BY created_at", (clinic_id,)).fetchall()]
        workflow_status = clinic.get("status", "DISCOVERED")
        if draft:
            if draft["status"] == "APPROVED": workflow_status = "READY_TO_SEND" if any(int(row["id"]) == int(draft["id"]) for row in self.ready_to_send()) else "APPROVED"
            elif draft["status"] == "DRAFTED": workflow_status = "LETTER_DONE"
            else: workflow_status = draft["status"]
        return {"clinic": clinic, "analysis": analysis, "analysis_snapshot": analysis_snapshot, "analysis_history": history, "draft": draft, "workflow_status": workflow_status, "contacts": contacts, "evidence": clinic["profile"].get("evidence", [])}

    def list_contacts(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT ct.*,c.name clinic_name,c.website,c.status clinic_status FROM contacts ct JOIN clinics c ON c.id=ct.clinic_id ORDER BY ct.created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def list_suppression(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM suppression_list ORDER BY created_at DESC").fetchall()]

    def remove_suppression(self, email: str) -> None:
        normalized = email.lower()
        self.conn.execute("DELETE FROM suppression_list WHERE email=?", (normalized,)); self.conn.commit()
        try:
            items = json.loads(self.suppression_path.read_text(encoding="utf-8")) if self.suppression_path.exists() else []
            self.suppression_path.write_text(json.dumps([x for x in items if x != normalized], ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def list_send_logs(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("""SELECT l.*,d.subject,c.name clinic_name FROM send_logs l
          LEFT JOIN drafts d ON d.id=l.draft_id LEFT JOIN clinics c ON c.id=d.clinic_id ORDER BY l.created_at DESC""").fetchall()
        return [dict(row) for row in rows]

    def recent_activity(self) -> list[dict[str, Any]]:
        events = []
        for row in self.conn.execute("SELECT name clinic_name,collected_at timestamp,'DISCOVERED' type FROM clinics ORDER BY collected_at DESC LIMIT 10"):
            events.append(dict(row))
        for row in self.conn.execute("SELECT c.name clinic_name,a.created_at timestamp,'AI_ANALYSIS_COMPLETED' type FROM analyses a JOIN clinics c ON c.id=a.clinic_id ORDER BY a.created_at DESC LIMIT 10"):
            events.append(dict(row))
        for row in self.conn.execute("SELECT c.name clinic_name,d.created_at timestamp,'EMAIL_DRAFT_GENERATED' type FROM drafts d JOIN clinics c ON c.id=d.clinic_id ORDER BY d.created_at DESC LIMIT 10"):
            events.append(dict(row))
        for row in self.conn.execute("SELECT recipient clinic_name,created_at timestamp,status type FROM send_logs ORDER BY created_at DESC LIMIT 10"):
            events.append(dict(row))
        for row in self.conn.execute("SELECT timestamp,vertical,source,status,discovered,'DISCOVERY' type FROM discovery_runs ORDER BY timestamp DESC LIMIT 10"):
            item = dict(row); item["clinic_name"] = f"{item.pop('vertical')} · {item.pop('source')}"; item["message"] = f"{item['status']}: {item['discovered']} candidate(s)"; events.append(item)
        return sorted(events, key=lambda x: x["timestamp"], reverse=True)[:10]

    def dashboard(self) -> dict[str, int]:
        counts = {"clinics": 0, "contacts": 0, "analyzed": 0, "drafted": 0, "approved": 0, "sent": 0, "failed": 0, "suppressed": 0, "qualified": 0, "needs_review": 0, "disqualified": 0}
        for key, sql in [("clinics", "SELECT COUNT(*) FROM clinics"), ("contacts", "SELECT COUNT(*) FROM contacts"), ("drafted", "SELECT COUNT(*) FROM drafts WHERE status IN ('DRAFTED','APPROVED')"), ("approved", "SELECT COUNT(*) FROM drafts WHERE status='APPROVED'"), ("sent", "SELECT COUNT(*) FROM send_logs WHERE status='SENT'"), ("failed", "SELECT COUNT(*) FROM drafts WHERE status IN ('FAILED','SEND_FAILED','SEND_INTERRUPTED')"), ("suppressed", "SELECT COUNT(*) FROM suppression_list")]:
            if key == "contacts": counts["contacts"] = self.conn.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
            else: counts[key] = self.conn.execute(sql).fetchone()[0]
        counts["analyzed"] = self.conn.execute("SELECT COUNT(DISTINCT clinic_id) FROM analyses").fetchone()[0]
        counts["researched"] = self.conn.execute("SELECT COUNT(*) FROM clinics WHERE status IN ('RESEARCHED','ANALYZED','QUALIFIED','NEEDS_REVIEW','DISQUALIFIED','DRAFTED','APPROVED','READY_TO_SEND')").fetchone()[0]
        for row in self.conn.execute("SELECT status,profile_json FROM clinics"):
            try: qualification = json.loads(row["profile_json"] or "{}").get("qualification", {}).get("status")
            except (json.JSONDecodeError, AttributeError): qualification = None
            qualification = qualification or (row["status"] if row["status"] in {"QUALIFIED", "NEEDS_REVIEW", "DISQUALIFIED"} else None)
            if qualification in {"QUALIFIED", "NEEDS_REVIEW", "DISQUALIFIED"}: counts[qualification.lower()] += 1
        counts["ready_to_review"] = self.conn.execute("SELECT COUNT(*) FROM drafts WHERE status='DRAFTED'").fetchone()[0]
        counts["letters_done"] = self.conn.execute("SELECT COUNT(*) FROM drafts WHERE status IN ('DRAFTED','APPROVED')").fetchone()[0]
        counts["ready_to_send"] = len(self.ready_to_send())
        return counts

    def update_draft(self, draft_id: int, **fields: Any) -> None:
        allowed = {k: v for k, v in fields.items() if k in {"subject", "body", "status"}}
        if not allowed: return
        assignments = [k+'=?' for k in allowed]
        values = list(allowed.values())
        if "body" in allowed:
            assignments.append("plain_text_body=?"); values.append(allowed["body"])
            assignments.append("html_body=?"); values.append("")
        values += [utc_now(), draft_id]
        self.conn.execute(f"UPDATE drafts SET {', '.join(assignments)}, updated_at=? WHERE id=?", values); self.conn.commit()

    def is_suppressed(self, email: str) -> bool:
        return self.conn.execute("SELECT 1 FROM suppression_list WHERE email=?", (email.lower(),)).fetchone() is not None

    def suppress(self, email: str, reason: str = "manual") -> None:
        normalized = email.lower()
        self.conn.execute("INSERT OR REPLACE INTO suppression_list(email,reason,created_at) VALUES(?,?,?)", (normalized, reason, utc_now())); self.conn.execute("UPDATE drafts SET status='DO_NOT_CONTACT',updated_at=? WHERE contact_id IN (SELECT id FROM contacts WHERE email=?) AND status IN ('DRAFTED','APPROVED')", (utc_now(), normalized)); self.conn.commit()
        try:
            items = json.loads(self.suppression_path.read_text(encoding="utf-8")) if self.suppression_path.exists() else []
            if normalized not in items: items.append(normalized)
            self.suppression_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def get_draft(self, draft_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT d.*,ct.email,ct.contact_type,ct.is_public,ct.source_url email_source,c.name clinic_name,c.status clinic_status FROM drafts d JOIN contacts ct ON ct.id=d.contact_id JOIN clinics c ON c.id=d.clinic_id WHERE d.id=?", (draft_id,)).fetchone()

    def log_send(self, draft_id: int, recipient: str, provider: str, status: str, error: str | None = None) -> None:
        self.conn.execute("INSERT INTO send_logs(draft_id,recipient,provider,status,error,created_at) VALUES(?,?,?,?,?,?)", (draft_id, recipient, provider, status, error, utc_now())); self.conn.commit()
