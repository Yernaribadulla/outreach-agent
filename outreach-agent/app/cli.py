from __future__ import annotations

import argparse
import csv
import json
import sys
import os
from html import escape
from pathlib import Path

from .discovery.engine import discover
from .discovery.providers import PublicSourceProvider, TwoGISProvider, YandexProvider, WebProvider, PlaywrightTwoGISProvider, PlaywrightWebProvider, OpenStreetMapProvider
from .discovery.real_candidates import REAL_ASTANA_CANDIDATES
from .verticals import get_vertical
from .analysis.lm_studio import LMStudioClient, LMStudioError
from .generation.email_draft import generate_draft
from .discovery.gemini_import import import_gemini_file
from .autonomous import MODEL, run_autonomous
from .storage.db import Database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local B2B Lead Discovery & Outreach Agent")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("discover"); d.add_argument("--vertical", default="dental"); d.add_argument("--target", type=int, default=10); d.add_argument("--city", default="Астана"); d.add_argument("--out", default="data"); d.add_argument("--mode", choices=("live", "offline"), default="live"); d.add_argument("--provider", choices=("default", "osm", "playwright_2gis", "playwright_web"), default="default"); d.add_argument("--research", action="store_true")
    p = sub.add_parser("probe"); p.add_argument("--provider", choices=("2gis", "yandex", "web"), required=True); p.add_argument("--city", default="Астана"); p.add_argument("--query", default="стоматология"); p.add_argument("--target", type=int, default=5)
    i = sub.add_parser("import-discovery"); i.add_argument("--vertical", default="dental"); i.add_argument("--file", required=True); i.add_argument("--db", default="data/outreach.db")
    a = sub.add_parser("autonomous"); a.add_argument("--vertical", default="dental"); a.add_argument("--city", default="Астана"); a.add_argument("--target", type=int, default=100); a.add_argument("--db", default="data/outreach.db"); a.add_argument("--dry-run", action="store_true"); a.add_argument("--send", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "import-discovery":
        try:
            result = import_gemini_file(args.file, args.vertical, args.db)
            print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(json.dumps({"status": "IMPORT_ERROR", "error": str(exc)}, ensure_ascii=False, indent=2)); return 2
    if args.command == "autonomous":
        if args.send: print(json.dumps({"status": "SEND_DISABLED", "real_email_sends": 0}, ensure_ascii=False)); return 2
        print(json.dumps(run_autonomous(args.vertical, args.city, args.target, args.db, dry_run=True), ensure_ascii=False, indent=2)); return 0
    if args.command == "probe":
        providers = {"2gis": TwoGISProvider(), "yandex": YandexProvider(), "web": WebProvider()}
        provider = providers[args.provider]
        try:
            results = provider.search(f"{args.query} {args.city}", args.target)
            print(json.dumps({"provider": args.provider, "status": "SUCCESS", "count": len(results), "candidates": [{"name": x.get("name"), "source_url": x.get("source_url")} for x in results[:5]]}, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(json.dumps({"provider": args.provider, "status": "SOURCE_UNAVAILABLE", "error_type": type(exc).__name__, "reason": str(exc)[:240]}, ensure_ascii=False, indent=2))
        return 0
    if args.command != "discover": return 2
    if args.provider == "playwright_2gis":
        try:
            results = PlaywrightTwoGISProvider().search(f"стоматология {args.city}", args.target)
            print(json.dumps({"provider": "PlaywrightTwoGIS", "status": "SUCCESS", "count": len(results), "candidates": results[:5]}, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(json.dumps({"provider": "PlaywrightTwoGIS", "status": "SOURCE_UNAVAILABLE", "error_type": type(exc).__name__, "reason": str(exc)[:240]}, ensure_ascii=False, indent=2))
        return 0
    if args.provider == "playwright_web":
        try:
            results = PlaywrightWebProvider().search(f"стоматология {args.city}", args.target)
            print(json.dumps({"provider": "PlaywrightWeb", "status": "SUCCESS", "count": len(results), "candidates": results[:5]}, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(json.dumps({"provider": "PlaywrightWeb", "status": "SOURCE_UNAVAILABLE", "error_type": type(exc).__name__, "reason": str(exc)[:240]}, ensure_ascii=False, indent=2))
        return 0
    vertical = get_vertical(args.vertical)
    if vertical.key != "dental":
        print(f"Vertical {vertical.key} configured, but no live provider records are enabled yet.", file=sys.stderr); return 2
    if getattr(args, "provider", "default") == "osm":
        providers = [OpenStreetMapProvider()]
    elif args.mode == "offline":
        providers = [PublicSourceProvider("reviewed project records", REAL_ASTANA_CANDIDATES)]
    else:
        providers = [PlaywrightWebProvider(), PlaywrightTwoGISProvider(), TwoGISProvider(), YandexProvider(), WebProvider()]
    run = discover(providers, vertical.key, f"{vertical.search_terms[0]} {args.city}", args.target, audit_websites=bool(args.research))
    osm_stats = None
    if getattr(args, "provider", "default") == "osm":
        db = Database("data/outreach.db"); before = {row["id"] for row in db.conn.execute("SELECT id FROM clinics")}
        for lead in run.candidates:
            db.add_clinic({"name": lead.get("name"), "website": lead.get("website"), "city": lead.get("city"), "phone": lead.get("phone"), "category": lead.get("category"), "source_url": lead.get("source_url"), "description": lead.get("description"), "profile": lead})
        after = {row["id"] for row in db.conn.execute("SELECT id FROM clinics")}; db.close()
        osm_stats = {"provider": "openstreetmap", "city": args.city, "received": len(run.candidates), "new": len(after - before), "duplicates": len(run.candidates) - len(after - before), "total_companies": len(after)}
    lm_analyses = 0; drafts = 0
    source_mode = args.mode
    for lead in run.candidates:
        lead["source_mode"] = source_mode
        lead["provider"] = "PublicSourceProvider" if source_mode == "offline" else "live providers"
        provenance_url = lead.get("contact_source") or (lead.get("source_urls") or [lead.get("website")])[0]
        contacts = list(lead.get("contacts", []))
        if lead.get("email") and not any(x.get("type") == "PUBLIC_BUSINESS_EMAIL" and x.get("value") == lead["email"] for x in contacts):
            contacts.append({"type": "PUBLIC_BUSINESS_EMAIL", "value": lead["email"], "source": provenance_url, "confidence": "HIGH"})
        if lead.get("phone") and not any(x.get("type") == "PUBLIC_BUSINESS_PHONE" and x.get("value") == lead["phone"] for x in contacts):
            contacts.append({"type": "PUBLIC_BUSINESS_PHONE", "value": lead["phone"], "source": provenance_url, "confidence": "HIGH"})
        lead["contacts"] = contacts
    if not args.research:
        base_url = None
    else:
        base_url = os.environ.get("LM_STUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
    model = os.environ.get("LM_STUDIO_MODEL", MODEL)
    if model != MODEL and args.research:
        print(json.dumps({"status": "AI_UNAVAILABLE", "error": f"Only {MODEL} is supported"}, ensure_ascii=False)); return 2
    try:
        if base_url is None: raise LMStudioError("research stage not requested")
        client = LMStudioClient(base_url, model); client.health()
        for lead in run.candidates:
            payload = {"company": {k: lead.get(k) for k in ("name", "city", "address", "website")}, "sources": lead.get("sources", []), "contacts": lead.get("contacts", []), "website_audit": lead.get("website_audit", {}), "evidence": lead.get("evidence", [])}
            try:
                lead["lm_analysis"] = client.chat_opportunity(payload); lm_analyses += 1
                lead["sales_brief"] = {"company": lead.get("name"), "location": lead.get("address") or lead.get("city"), "sources": lead.get("sources", []), "website": lead.get("website"), "contacts": lead.get("contacts", []), "digital_state": lead["lm_analysis"].get("digital_state", {}), "recommended_angle": lead["lm_analysis"].get("recommended_angle"), "why_this_lead": lead["lm_analysis"].get("why_this_lead", []), "evidence": lead.get("evidence", [])}
                email = next((x.get("value") for x in lead.get("contacts", []) if x.get("type") == "PUBLIC_BUSINESS_EMAIL"), None)
                lead["email"] = email
                if email:
                    draft = generate_draft(client, lead, lead["lm_analysis"], {"sender_name": "", "sender_brand": "DENTARA", "sender_contact": ""})
                    body = draft.get("body", ""); draft["plain_text"] = body; draft["html"] = f'<div style="font:15px Arial;line-height:1.6">{escape(body).replace(chr(10), "<br>")}</div>'; lead["email_draft"] = draft; drafts += 1
            except (LMStudioError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                lead["lm_status"] = "LM_STUDIO_ERROR"; lead["lm_error"] = str(exc)[:200]
    except Exception as exc:
        run.activity.append({"type": "LM_UNAVAILABLE", "error": str(exc)[:200]})
    output = Path(args.out); output.mkdir(parents=True, exist_ok=True)
    (output / "leads.json").write_text(json.dumps({"vertical": vertical.key, "source_mode": source_mode, "provider": "PublicSourceProvider" if source_mode == "offline" else "live providers", "source_status": run.source_status, "activity": run.activity, "leads": run.candidates}, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "leads.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        fields = ["name", "city", "address", "website", "website_status", "phone", "email", "same_business_confidence"]
        writer = csv.DictWriter(fh, fieldnames=fields); writer.writeheader(); writer.writerows({k: lead.get(k, "") for k in fields} for lead in run.candidates)
    result = {"vertical": vertical.key, "count": len(run.candidates), "duplicates_removed": sum(1 for x in run.activity if x.get("type") == "RESOLUTION_COMPLETE" for _ in range(x.get("duplicates_removed", 0))), "source_status": run.source_status, "lm_analyses": lm_analyses, "drafts": drafts, "outputs": [str(output / "leads.json"), str(output / "leads.csv")]}
    if osm_stats: result["osm"] = osm_stats
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
