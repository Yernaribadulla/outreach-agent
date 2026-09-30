# Outreach Agent — local-first MVP

Outreach Agent is a working local-first B2B sales-intelligence MVP. It connects public discovery, entity resolution, website audit, evidence/provenance, local Qwen analysis, deterministic qualification, personalized draft generation, human review, and explicitly started sending in one durable SQLite workflow.

The MVP is functional end to end. The code architecture is still evolving and is not yet as clean or modular as a mature production system; see **Technical debt** below.

## What works

- Public discovery by vertical and geography, including dental and detailing configuration.
- Deduplication and entity resolution with source provenance preserved.
- Website and contact research with deterministic feature detection.
- Evidence-first analysis through LM Studio using only `qwen/qwen3-vl-8b`.
- Structured JSON validation, one bounded retry for malformed output, and `AI_ERROR` separated from `NOT_QUALIFIED`.
- Qualification based on evidence, opportunity fit, public contactability, ICP fit, and confidence.
- Personalized plain-text and HTML drafts with rationale and source observations.
- Durable nightly batch jobs with progress, per-company failures, explicit resume, and no automatic restart continuation.
- Review Queue with persisted selection, **Выбрать всех**, **Снять всех**, evidence, rationale, and email preview.
- Approval remains separate from sending.
- Simulated sending and explicit backend-controlled sequential sending with locks, safety checks, rate limits, interruption, and resume.
- Historical `SENT` records and SQLite data are preserved across startup and migrations.

## Run locally

From this directory:

```bash
python -m app.server
```

Open `http://127.0.0.1:8765`. By default the database is `data/outreach.db`. Override it locally with `OUTREACH_DB_PATH`; override the port with `OUTREACH_PORT`.

LM Studio must expose `http://127.0.0.1:1234/v1` with the exact model `qwen/qwen3-vl-8b`. The application does not switch to a fallback model.

## Evening workflow

On Overview choose a vertical, city/geography, and company count, then press **🚀 Запустить ночной поиск**. The backend worker runs:

```text
DISCOVERY → DEDUPLICATION → RESEARCH → WEBSITE AUDIT → EVIDENCE
→ QWEN ANALYSIS → QUALIFICATION → DRAFT GENERATION → READY FOR REVIEW
```

The browser is not required after launch. The worker continues while the backend process is running. A failure for one company is recorded and does not stop the remaining companies. Progress, counters, stages, and error details are stored in SQLite.

After a backend restart, an unfinished job is marked interrupted and is not resumed automatically. The user must explicitly choose **Продолжить** or **Остановить**.

Existing analyses and drafts are idempotent by default. Re-analysis and regeneration are explicit actions, and analysis history is retained.

## Morning review and sending

Qualified drafts appear in Review Queue as compact cards. Eligible cards are selected by default. Selection is persisted in SQLite and survives refresh. Each card can show clinic, city, website, public business email, priority, WHY THIS LEAD, qualification reasons, recommended angle, opportunity details, plain text, HTML preview, rationale, and supporting evidence.

To send, leave only the desired cards selected and press **ОТПРАВИТЬ N ПИСЕМ**. The backend creates an immutable snapshot, validates each recipient, and sends sequentially. Closing the browser does not stop the worker. A stop request finishes the current safe operation and marks the batch `SEND_INTERRUPTED`; resuming always requires an explicit user action.

## Sending modes and safety

`SIMULATED_SEND=true` is the safe default. It exercises the full send workflow without SMTP network delivery.

For an explicit real-send session, set `SIMULATED_SEND=false` and configure the existing local, untracked environment variables: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM`.

Then choose **REAL SMTP**, review the recipient count, sender, and delay range, and confirm the modal. The application never logs or displays the password. Approval never sends an email. Real sending is never started automatically.

Before each send the backend checks approval/readiness, public business email provenance, suppression status, `DO_NOT_CONTACT`, duplicate send history, send locks, SMTP configuration, and the selected immutable snapshot.

## Problems fixed in this MVP

### Analysis and qualification

Successful AI output is stored as `ANALYZED`; qualification runs separately and produces `QUALIFIED`, `NEEDS_REVIEW`, or a rejected outcome. Missing confidence or insufficient evidence does not silently become qualified.

### Evidence and Qwen JSON

Evidence is canonicalized and deduplicated. Qwen receives a compact research object with at most 8,000 characters and four relevant evidence items; full evidence remains in SQLite. Opportunity analysis uses JSON Schema when supported, validates schema and evidence references, and performs at most one strict retry. Two invalid responses become `AI_ERROR` instead of guessed content.

### Website claims

Website evidence keeps `chat_widget`, `ai_assistant`, booking, payment, CRM, and automation distinct. A chat marker can be confirmed without claiming that the chat is AI-powered.

### Review Queue contract

Older analyses may contain scalar `why_this_lead` values while newer analyses contain arrays. The Review Queue now renders the supported shapes correctly and preserves empty states for optional evidence, contacts, sources, and opportunities.

### Selection and sending

Selection is a separate persisted state. Select-all and deselect-all do not change qualification. Sending snapshots selected draft IDs before the backend worker starts, preventing checkbox changes or duplicate clicks from altering an active send batch.

## Verification

Run from `outreach-agent`:

```bash
node --check app/ui/review.js
python -m compileall -q app tests
python -m unittest discover -s tests -v
git diff --check
```

The regression suite covers discovery/API contracts, evidence ownership and deduplication, Qwen JSON parsing and retry behavior, qualification, draft persistence, queue selection, approval, simulated and mocked SMTP sending, send locks, interruption/resume, and safety checks.

## Technical debt

This is a working local-first MVP, not a finished production platform. Responsibilities are still mixed between the HTTP handler, worker orchestration, SQLite access, and UI rendering. Some historical records use older shapes, configuration loading is intentionally lightweight, and provider integrations remain bounded by public-source availability. Future cleanup should introduce clearer service boundaries, typed API schemas, migrations/versioning, and more isolated worker modules without casually changing the current safety behavior or data model.

Do not enable real SMTP until the intended recipient set, evidence, drafts, provider limits, and confirmation preview have been reviewed by a human.

Если при использовании будут некоторые недочёты или баги, пожалуйста обратитесь ко мне на гитхаб
