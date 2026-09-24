# Outreach Agent

`outreach-agent` is a local-first B2B sales-intelligence and outreach pipeline. It discovers public businesses, preserves source evidence and provenance, audits websites, sends structured evidence to a local LM Studio model, qualifies digital opportunities, and prepares personalized outreach drafts for review.

The project is provider-neutral and uses only public business information. It does not guess contacts, bypass CAPTCHA or anti-bot systems, or send email automatically during development.

## MVP architecture

```text
OpenStreetMap / Nominatim / Overpass
        ↓
SQLite
        ↓
Research
        ↓
Website audit / evidence
        ↓
Local LM Studio
        ↓
Opportunity qualification
        ↓
Email draft validation
```

The current implementation includes OpenStreetMap discovery through Nominatim and Overpass, provider-neutral discovery and entity resolution, SQLite persistence, deterministic website audit and public contact extraction, Gemini discovery JSON import with provenance, local LM Studio analysis, and review-oriented draft/safety boundaries.

## Local LM Studio

LM Studio is used locally at `http://127.0.0.1:1234/v1` with the required model `qwen/qwen3-vl-8b`. No fallback model is configured by this project.

## Discovery

The current live discovery provider is OpenStreetMap via Nominatim and Overpass:

```powershell
python -m app.cli discover --vertical dental --city Астана --target 100 --provider osm
```

The command records public discovery evidence, resolves duplicates, stores results in the existing SQLite database, and reports provider statistics. Missing fields remain unknown; OSM claims are discovery evidence, not automatically verified facts.

Gemini discovery JSON can also be imported explicitly without starting providers or AI analysis:

```powershell
python -m app.cli import-discovery --vertical dental --file gemini_batch_001.json
```

## Autonomous dry-run

The autonomous command prepares research and drafts without sending real email:

```powershell
python -m app.cli autonomous --vertical dental --city Астана --target 5 --dry-run
```

## Current Status

This project is currently under active development. The discovery layer is operational and has been tested against live OpenStreetMap/Nominatim/Overpass services. The autonomous pipeline MVP is implemented, but the full overnight workflow is not yet considered production-ready.

Current limitations:

- discovery works and persists records in the existing SQLite database;
- autonomous dry-run starts and processes independent leads;
- website audit and research can take significant time for a large batch;
- checkpoint/resume is not yet a complete overnight workflow;
- individual pipeline error diagnostics and batch resilience are still being improved;
- real email sending is not a guaranteed or production-ready project capability.

## Safety / Sending

Current validation is performed in dry-run mode. No real email sends have been performed during development or testing. Email sending is not considered a production-ready workflow.

Credentials and secrets must remain outside Git. Do not commit `.env`, SMTP passwords, API keys, tokens, or local secret files. SMTP is disabled by default, and the simulated sender must not be confused with real delivery.

## Development

```powershell
python -m compileall -q app tests
python -m unittest discover -s tests -v
git diff --check
```

The tests cover provider failure handling, entity resolution, provenance, website audit behavior, Gemini import behavior, LM response parsing, suppression, and sender safety boundaries.

## Privacy and source policy

Only public business information should be collected. The project must not guess email addresses, use private personal data, bypass CAPTCHA/anti-bot controls, or treat an unverified discovery claim as a confirmed fact. Useful claims should retain their source URL and confidence/status where available.
