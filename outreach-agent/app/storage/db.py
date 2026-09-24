from __future__ import annotations

import json
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
        """)
        self._ensure_column("clinics", "profile_json", "TEXT DEFAULT '{}'" )
        self._ensure_column("contacts", "contact_type", "TEXT DEFAULT 'PUBLIC_BUSINESS_EMAIL'")
        self._ensure_column("contacts", "source_name", "TEXT DEFAULT ''")
        self._ensure_column("contacts", "confidence", "TEXT DEFAULT 'confirmed_by_source'")
        self._ensure_column("contacts", "is_public", "INTEGER DEFAULT 1")
        self._ensure_column("drafts", "html_body", "TEXT DEFAULT ''")
        self._ensure_column("drafts", "plain_text_body", "TEXT DEFAULT ''")
        self.conn.execute("UPDATE drafts SET status='SENT', updated_at=? WHERE id IN (SELECT draft_id FROM send_logs WHERE status='SENT' AND draft_id IS NOT NULL)", (utc_now(),))
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        existing = {row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing: self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def close(self) -> None:
        self.conn.close()

    def add_clinic(self, clinic: dict[str, Any]) -> int:
        existing = self.conn.execute("SELECT id FROM clinics WHERE lower(name)=lower(?) AND lower(coalesce(city,''))=lower(coalesce(?,'')) LIMIT 1", (clinic.get("name"), clinic.get("city"))).fetchone()
        if existing:
            self.conn.execute("""UPDATE clinics SET website=?, country=?, category=?, phone=?, contact_page=?, description=?, source_url=?, profile_json=?, collected_at=? WHERE id=?""", (clinic.get("website"), clinic.get("country"), clinic.get("category"), clinic.get("phone"), clinic.get("contact_page"), clinic.get("description"), clinic.get("source_url"), json.dumps(clinic.get("profile", clinic), ensure_ascii=False), utc_now(), int(existing[0])))
            self.conn.commit()
            return int(existing[0])
        cur = self.conn.execute("""INSERT INTO clinics(name,website,city,country,category,phone,contact_page,description,source_url,collected_at,profile_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?)""", (clinic.get("name"), clinic.get("website"), clinic.get("city"), clinic.get("country"), clinic.get("category"), clinic.get("phone"), clinic.get("contact_page"), clinic.get("description"), clinic.get("source_url"), utc_now(), json.dumps(clinic.get("profile", clinic), ensure_ascii=False)))
        self.conn.commit(); return int(cur.lastrowid)

    def list_discovery_runs(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM discovery_runs ORDER BY timestamp DESC").fetchall()]

    def add_contact(self, clinic_id: int, email: str, source_url: str, kind: str = "business") -> int | None:
        try:
            cur = self.conn.execute("INSERT INTO contacts(clinic_id,email,source_url,kind,created_at,contact_type,source_name,confidence,is_public) VALUES(?,?,?,?,?,?,?,?,?)", (clinic_id, email.lower(), source_url, kind, utc_now(), "PUBLIC_BUSINESS_EMAIL", kind, "confirmed_by_source", 1))
            self.conn.commit(); return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            row = self.conn.execute("SELECT id FROM contacts WHERE email=?", (email.lower(),)).fetchone()
            return int(row[0]) if row else None

    def add_analysis(self, clinic_id: int, model: str, snapshot: dict[str, Any], result: dict[str, Any]) -> int:
        cur = self.conn.execute("INSERT INTO analyses(clinic_id,model,input_snapshot,result_json,created_at) VALUES(?,?,?,?,?)", (clinic_id, model, json.dumps(snapshot, ensure_ascii=False), json.dumps(result, ensure_ascii=False), utc_now()))
        self.conn.execute("UPDATE clinics SET status='ANALYZED' WHERE id=?", (clinic_id,)); self.conn.commit(); return int(cur.lastrowid)

    def add_draft(self, clinic_id: int, contact_id: int, draft: dict[str, Any]) -> int:
        cur = self.conn.execute("""INSERT INTO drafts(clinic_id,contact_id,subject,body,rationale,source_observations,confidence,status,created_at,updated_at,html_body,plain_text_body)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (clinic_id, contact_id, draft["subject"], draft["body"], draft["rationale"], json.dumps(draft.get("source_observations", []), ensure_ascii=False), float(draft.get("confidence", 0)), "DRAFTED", utc_now(), utc_now(), draft.get("html_body", ""), draft.get("plain_text_body", draft["body"])))
        self.conn.execute("UPDATE clinics SET status='DRAFTED' WHERE id=?", (clinic_id,)); self.conn.commit(); return int(cur.lastrowid)

    def list_queue(self, status: str | None = None) -> list[dict[str, Any]]:
        where = "WHERE d.status=?" if status else ""
        args = (status,) if status else ()
        rows = self.conn.execute(f"""SELECT d.*, c.name clinic_name,c.website,c.city,c.country,c.source_url,c.description,c.phone,c.profile_json,ct.email,ct.source_url email_source,ct.contact_type,ct.source_name,ct.confidence contact_confidence
          FROM drafts d JOIN clinics c ON c.id=d.clinic_id JOIN contacts ct ON ct.id=d.contact_id {where} ORDER BY d.updated_at DESC""", args).fetchall()
        result = []
        for row in rows:
            item = dict(row); item["source_observations"] = json.loads(item["source_observations"]); result.append(item)
        return result

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
                item.update({"website_status": profile.get("website_status", "WEBSITE_UNCERTAIN"), "website_quality_observations": profile.get("website_quality_observations", []), "services": profile.get("services", []), "source_urls": profile.get("source_urls", [])})
            except json.JSONDecodeError: pass
            result.append(item)
        return result

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
        for row in self.conn.execute("SELECT recipient clinic_name,created_at timestamp,status type FROM send_logs ORDER BY created_at DESC LIMIT 10"):
            events.append(dict(row))
        return sorted(events, key=lambda x: x["timestamp"], reverse=True)[:10]

    def dashboard(self) -> dict[str, int]:
        counts = {"clinics": 0, "contacts": 0, "analyzed": 0, "drafted": 0, "approved": 0, "sent": 0, "failed": 0, "suppressed": 0}
        for key, sql in [("clinics", "SELECT COUNT(*) FROM clinics"), ("contacts", "SELECT COUNT(*) FROM contacts"), ("analyzed", "SELECT COUNT(*) FROM analyses"), ("drafted", "SELECT COUNT(*) FROM drafts WHERE status NOT IN ('SENT','FAILED')"), ("approved", "SELECT COUNT(*) FROM drafts WHERE status='APPROVED'"), ("sent", "SELECT COUNT(*) FROM drafts WHERE status='SENT'"), ("failed", "SELECT COUNT(*) FROM drafts WHERE status='FAILED'"), ("suppressed", "SELECT COUNT(*) FROM suppression_list")]: counts[key] = self.conn.execute(sql).fetchone()[0]
        return counts

    def update_draft(self, draft_id: int, **fields: Any) -> None:
        allowed = {k: v for k, v in fields.items() if k in {"subject", "body", "status"}}
        if not allowed: return
        values = list(allowed.values()) + [utc_now(), draft_id]
        self.conn.execute(f"UPDATE drafts SET {', '.join(k+'=?' for k in allowed)}, updated_at=? WHERE id=?", values); self.conn.commit()

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
        return self.conn.execute("SELECT d.*,ct.email,c.name clinic_name FROM drafts d JOIN contacts ct ON ct.id=d.contact_id JOIN clinics c ON c.id=d.clinic_id WHERE d.id=?", (draft_id,)).fetchone()

    def log_send(self, draft_id: int, recipient: str, provider: str, status: str, error: str | None = None) -> None:
        self.conn.execute("INSERT INTO send_logs(draft_id,recipient,provider,status,error,created_at) VALUES(?,?,?,?,?,?)", (draft_id, recipient, provider, status, error, utc_now())); self.conn.commit()
