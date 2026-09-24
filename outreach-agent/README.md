# outreach-agent

`outreach-agent` is a local-first B2B sales intelligence and outreach automation platform for discovering and researching potential business clients, building structured opportunity profiles, and preparing personalized outreach drafts.

The current MVP is designed for public business information and local execution. It keeps discovery records, technical observations, evidence, provenance, AI analysis, and draft state together so that a human can review the result before any future sending step.

## What the MVP contains

- Python application using the standard library;
- SQLite storage for businesses, contacts, analyses, drafts, suppression, send logs, and discovery runs;
- OpenStreetMap discovery using Nominatim and Overpass;
- local LM Studio integration;
- only the `qwen/qwen3-vl-8b` model is supported for the autonomous MVP;
- deterministic website audit and public contact extraction;
- entity resolution and deduplication;
- evidence and source provenance;
- configurable vertical definitions (`dental` is the current live vertical);
- an autonomous MVP and dry-run workflow;
- personalized outreach preparation without real email delivery.

AI analysis is not a source of truth. It must use supplied evidence, preserve provenance, distinguish confirmed and unknown states, and never invent contacts, websites, owners, technologies, or business problems.

## Pipeline

```text
Discovery
    → Entity Resolution
    → Research
    → Website Audit
    → Evidence / Provenance
    → AI Analysis
    → Qualification
    → Outreach Draft
    → Human Review
    → Future Sending
```

Deterministic facts are collected and checked in Python where possible. LM Studio is used for semantic opportunity analysis and draft generation after the evidence object has been assembled.

## Current Status

**Active development / MVP stage — not production-ready.**

The OpenStreetMap discovery layer is operational and has been tested against live Nominatim and Overpass services. SQLite storage works, local LM Studio integration is implemented, and the autonomous pipeline exists in MVP/dry-run form.

The complete overnight workflow still has important limitations:

- public web and business providers can return `CAPTCHA`, anti-bot challenges, or `SOURCE_UNAVAILABLE`;
- large-scale automated web research and website auditing can be slow and are not yet reliably unattended;
- OpenStreetMap is the current working free discovery source and does not provide complete contact or website coverage;
- checkpoint/resume is not yet a complete overnight workflow;
- batch resilience and diagnostics for individual pipeline errors are still being improved;
- real email sending is not a guaranteed or production-ready feature of this project.

The project intentionally does not bypass CAPTCHA or anti-bot protection, use stealth plugins, or use proxies to evade access controls.

## Installation

Prerequisites:

- Windows;
- Python 3.14+ (the code uses only the Python standard library; `requirements.txt` documents this);
- Git;
- LM Studio if AI analysis is required;
- a locally available `qwen/qwen3-vl-8b` model in LM Studio.

Clone the repository and create a virtual environment:

```powershell
git clone https://github.com/Yernaribadulla/outreach-agent.git
cd outreach-agent
python -m venv .venv
.venv\Scripts\activate
```

No third-party installation is required by the current `requirements.txt`.

## LM Studio

For AI analysis:

1. Start LM Studio.
2. Load `qwen/qwen3-vl-8b`.
3. Start the local OpenAI-compatible API server.
4. Keep the server available at `http://127.0.0.1:1234/v1`.

The autonomous implementation uses this endpoint and this model. No fallback model is configured.

## Discovery

Run live dental discovery through OpenStreetMap/Nominatim/Overpass:

```powershell
python -m app.cli discover --vertical dental --city Астана --target 100 --provider osm
```

The command resolves the city through Nominatim, queries Overpass for `amenity=dentist` and `healthcare=dentist`, normalizes public fields, resolves duplicates, stores OSM discovery records in the existing SQLite database, and reports provider statistics. Missing fields remain `null`; OSM claims are discovery evidence, not automatically verified facts.

The repository also retains HTTP and browser provider implementations for explicit diagnostics. Providers may report `SOURCE_UNAVAILABLE` when public access is blocked or unreliable.

## Autonomous dry-run

Prepare a local autonomous run without sending email:

```powershell
python -m app.cli autonomous --vertical dental --city Астана --target 5 --dry-run
```

The MVP orchestration uses OSM discovery, website research/audit, local LM Studio analysis, and draft validation. It is intended for development and review, not mass outreach.

## Gemini discovery import

Gemini discovery JSON can be imported as an input adapter:

```powershell
python -m app.cli import-discovery --vertical dental --file gemini_batch_001.json
```

Imported records retain `gemini_discovery` provenance and are not treated as verified automatically. SQLite/source evidence remains the source of truth; imported businesses should pass subsequent verification and audit steps.

## Email Safety

- Real email sending is disabled/not implemented for the current MVP workflow.
- Human review is required before any future sending step.
- Suppression and duplicate-recipient checks are part of the safety boundary and must be preserved.
- Discovery and development testing must not send email.
- SMTP credentials, API keys, passwords, tokens, and `.env` files must never be committed to Git.
- Local SQLite data and historical records must not be deleted as part of normal development.

The existing simulated sender is not real delivery. Do not treat a successful simulated operation as proof that production sending is ready.

## Project structure

```text
app/
├── cli.py                         CLI commands and output
├── autonomous.py                  MVP autonomous orchestration and draft validation
├── verticals.py                   Vertical configuration
├── website_audit.py               Deterministic website checks
├── analysis/lm_studio.py          Local LM Studio client and JSON analysis
├── discovery/                     Providers, engine, resolution, import, datasets
├── extraction/public_page.py      Public-page text and business email extraction
├── generation/email_draft.py      Structured draft generation
├── storage/db.py                  SQLite schema and persistence methods
├── email/sender.py                Simulated and guarded SMTP providers
└── ui/                            Existing local UI package
tests/                             Discovery, audit, import, and safety tests
```

## Tests and checks

Run the current checks from the repository root:

```powershell
python -m unittest discover -s tests -v
python -m compileall -q app tests
git diff --check
```

The test count can change as the MVP evolves. The suite covers provider failure handling, entity resolution, provenance, website audit behavior, Gemini import behavior, LM response parsing, suppression, simulated sending, and sender safety boundaries.

## Roadmap

- more reliable batch web research;
- complete checkpoint/resume for overnight runs;
- improved per-lead error diagnostics;
- more resilient website auditing;
- richer evidence and provenance views;
- deeper opportunity qualification;
- stronger human review UI;
- reply intelligence;
- production-grade sending only after separate validation of transport, limits, suppression, and review controls.

No delivery timeline is implied by this roadmap.
