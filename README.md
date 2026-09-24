# DENTARA + Outreach Agent

This repository contains two related but separate projects:

- **DENTARA** — a framework-free premium dental clinic demo site;
- **outreach-agent** — a local-first B2B sales intelligence and outreach preparation tool.

They share one Git repository, but their source directories and runtime responsibilities remain separate.

## DENTARA Dental Experience

DENTARA is a polished visual prototype for a fictional premium dental clinic. It combines editorial art direction, calm medical precision, responsive layouts, and a deliberate motion system into a standalone browser experience.

The current frontend includes:

- premium dental clinic experience and cinematic hero;
- responsive navigation and mobile menu;
- about/clinic story content;
- gallery and media interactions;
- doctors/team direction cards and profile interaction;
- services and treatment-direction sections;
- before/after results comparison and smile simulator modes;
- contacts section with map integration;
- FAQ accordion;
- multi-step consultation/booking flow with confirmation feedback;
- responsive desktop/mobile behavior;
- animated reveals, modal transitions, tactile CTA states, and reduced-motion support.

The demo is framework-free: HTML, CSS, and vanilla JavaScript only. It has no npm, React, Next.js, build system, or required server.

### Run DENTARA locally

From the repository root, open `index.html` in a modern browser. The project is designed to work directly from a `file://` URL.

All people, patients, statistics, addresses, testimonials, and treatment content are fictional or illustrative demo content. DENTARA is not a real clinic and is not medical advice.

## Outreach Agent

`outreach-agent` is a local-first B2B sales intelligence and outreach automation platform for discovering and researching potential business clients, building structured opportunity profiles, and preparing personalized outreach drafts.

The MVP uses Python, SQLite, OpenStreetMap/Nominatim/Overpass discovery, local LM Studio, website audit, entity resolution/deduplication, evidence/provenance, configurable verticals, an autonomous MVP pipeline, and dry-run outreach preparation without real email delivery.

### Pipeline

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

Deterministic facts are collected in Python where possible. AI analysis must use the supplied evidence, preserve provenance, distinguish confirmed and unknown states, and never invent contacts, websites, owners, technologies, or business problems.

### How the projects are related

DENTARA is the demonstration dental clinic website. `outreach-agent` is an internal B2B sales-intelligence and outreach tool for finding and analyzing potential business clients, including dental clinics. The projects are colocated in this repository but are not one application.

## Installation

### DENTARA

DENTARA requires no package installation or build step. Open the root `index.html` directly in a modern browser.

### Outreach Agent

Working directory:

```powershell
cd "C:\Users\user\Desktop\Codex LET ME COOK\outreach-agent"
```

Prerequisites: Windows, Python 3.14+, Git, LM Studio if AI analysis is needed, and a local `qwen/qwen3-vl-8b` model in LM Studio.

```powershell
python -m venv .venv
.venv\Scripts\activate
```

The current `outreach-agent/requirements.txt` states that no third-party packages are required; the implementation uses the Python standard library.

## LM Studio

For local AI analysis:

1. Start LM Studio.
2. Load exactly `qwen/qwen3-vl-8b`.
3. Start the local OpenAI-compatible API server.
4. Make it available at `http://127.0.0.1:1234/v1`.

No fallback model is configured.

## Outreach Agent discovery

Run live dental discovery through OpenStreetMap, Nominatim, and Overpass:

```powershell
python -m app.cli discover --vertical dental --city Астана --target 100 --provider osm
```

The command resolves the city, queries relevant dentist objects such as `amenity=dentist` and `healthcare=dentist`, normalizes public fields, resolves duplicates, stores discovery records in the existing SQLite database, and reports provider statistics. Missing values remain `null`; discovery claims are not automatically verified facts.

## Autonomous dry-run

```powershell
python -m app.cli autonomous --vertical dental --city Астана --target 5 --dry-run
```

The MVP orchestration uses OSM discovery, website research/audit, local LM Studio analysis, and draft validation. It is for development and review, not mass outreach. Real email sending is not performed by the normal dry-run workflow.

## Gemini discovery import

```powershell
python -m app.cli import-discovery --vertical dental --file gemini_batch_001.json
```

Imported records retain `gemini_discovery` provenance and are not treated as verified automatically. SQLite/source evidence remains the source of truth; imported businesses should pass further verification and audit.

## Current Status

**The repository and both projects are in active development. Neither project should be considered production-ready.**

For `outreach-agent`:

- OpenStreetMap/Nominatim/Overpass discovery is operational;
- SQLite storage is used for discovery and research records;
- local LM Studio integration is implemented;
- the autonomous pipeline exists in MVP/dry-run form;
- public business/web providers may return `CAPTCHA`, anti-bot challenges, or `SOURCE_UNAVAILABLE`;
- large batch website research and auditing can be slow and unreliable;
- checkpoint/resume is not yet a complete overnight workflow;
- individual pipeline error diagnostics and batch resilience still require improvement;
- the project intentionally does not bypass CAPTCHA or anti-bot protection;
- real email sending is not a guaranteed or production-ready capability.

For DENTARA, the current frontend is a polished demo experience, not a production medical service.

## Email Safety

- Real email sending is currently disabled/not implemented as a production workflow.
- Human review is required before any future sending step.
- Suppression and duplicate-recipient checks must be preserved.
- Discovery and development testing must not send email.
- SMTP credentials, API keys, passwords, tokens, and `.env` files must never be committed.
- The simulated sender is not real delivery.
- CAPTCHA and anti-bot controls are not bypassed.

## Project Structure

```text
README.md                    This combined repository documentation
index.html                   DENTARA page structure
styles.css                   DENTARA design system and responsive styling
script.js                    DENTARA interactions and animations
assets/                      DENTARA media assets
outreach-agent/
├── app/                     Outreach Agent application
├── data/                    Local database/runtime data
├── tests/                   Discovery, audit, import, and safety tests
├── requirements.txt         Standard-library dependency note
└── .gitignore               Local secrets and runtime exclusions
```

There is intentionally no `outreach-agent/README.md`; this root README is the single documentation entry point.

## Tests

```powershell
cd outreach-agent
python -m compileall -q app tests
python -m unittest discover -s tests -v
git diff --check
```

The suite covers provider failure handling, entity resolution, provenance, website audit behavior, Gemini import behavior, LM response parsing, suppression, simulated sending, and sender safety boundaries. The passing-test count can change as development continues.

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

No delivery timeline is implied.
