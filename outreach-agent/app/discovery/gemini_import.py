from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .resolution import match_confidence
from ..storage.db import Database, utc_now

FIELDS = ("name", "address", "phone", "website", "source_url", "source_type", "summary", "evidence", "confidence")


def _records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list): return payload
    if isinstance(payload, dict) and isinstance(payload.get("candidates"), list): return payload["candidates"]
    raise ValueError("Gemini JSON must be an array or an object with candidates[]")


def _normalize(item: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(item, dict): raise ValueError("Each Gemini candidate must be an object")
    result = {field: item.get(field) for field in FIELDS}
    if not result["name"]: raise ValueError("Gemini candidate name is required")
    if result["evidence"] is None: result["evidence"] = []
    result["sources"] = [{"source": "gemini_discovery", "source_url": result["source_url"], "source_type": result["source_type"], "confidence": result["confidence"]}]
    result["claim_status"] = "UNVERIFIED"
    return result


def import_gemini_file(path: str | Path, vertical: str, db_path: str | Path) -> dict[str, Any]:
    input_path = Path(path)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    incoming = [_normalize(item) for item in _records(payload)]
    db = Database(db_path)
    run_id = f"gemini-{uuid.uuid4().hex[:12]}"
    new_count = duplicate_count = 0
    existing_rows = db.conn.execute("SELECT * FROM clinics").fetchall()
    existing = []
    for row in existing_rows:
        item = dict(row); item.update(json.loads(item.get("profile_json") or "{}")); existing.append(item)
    for candidate in incoming:
        matches = [(match_confidence(candidate, row), row) for row in existing]
        matches = [(score, row) for score, row in matches if score >= 0.70]
        if matches:
            _, current = max(matches, key=lambda pair: pair[0])
            profile = json.loads(current.get("profile_json") or "{}")
            profile.setdefault("aliases", []).append(candidate["name"]) if candidate["name"] not in profile.setdefault("aliases", []) else None
            profile.setdefault("discovery_records", []).append(candidate)
            profile["duplicate_discoveries"] = int(profile.get("duplicate_discoveries", 0)) + 1
            db.conn.execute("UPDATE clinics SET profile_json=?, collected_at=? WHERE id=?", (json.dumps(profile, ensure_ascii=False), utc_now(), current["id"]))
            duplicate_count += 1
        else:
            profile = {"claim_status": "UNVERIFIED", "summary": candidate["summary"], "evidence": candidate["evidence"], "confidence": candidate["confidence"], "sources": candidate["sources"], "discovery_records": [candidate], "duplicate_discoveries": 0}
            clinic = {"name": candidate["name"], "address": candidate["address"], "website": candidate["website"], "phone": candidate["phone"], "source_url": candidate["source_url"], "description": candidate["summary"], "profile": profile, "city": None, "category": "dental"}
            clinic_id = db.add_clinic(clinic)
            candidate["id"] = clinic_id; existing.append({**clinic, "id": clinic_id, "profile_json": json.dumps(profile, ensure_ascii=False)})
            new_count += 1
    db.conn.execute("INSERT INTO discovery_runs(run_id,timestamp,vertical,source,input_file,received,new_count,duplicate_count) VALUES(?,?,?,?,?,?,?,?)", (run_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), vertical, "gemini_discovery", str(input_path), len(incoming), new_count, duplicate_count))
    db.conn.commit(); db.close()
    return {"run_id": run_id, "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "gemini_discovery", "input_file": str(input_path), "received": len(incoming), "new": new_count, "duplicates": duplicate_count, "total_companies": new_count + len(existing_rows)}
