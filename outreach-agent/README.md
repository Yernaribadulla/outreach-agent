# Outreach Agent

Local-first B2B sales-intelligence workspace. The existing dashboard and SQLite pipeline connect public-source discovery, evidence-backed analysis, qualification, draft generation, human review, and explicitly started sending.

## Run locally

From this directory:

```bash
python -m app.server
```

Open `http://127.0.0.1:8765`. The app stores its SQLite database under `data/outreach.db` by default. Set `OUTREACH_DB_PATH` or `OUTREACH_PORT` to use a different local database or port.

Analysis uses LM Studio at `http://127.0.0.1:1234/v1` and the required model `qwen/qwen3-vl-8b`. The dashboard does not fall back to another model.

## Batch analysis and draft generation

Use Discovery to persist candidate companies first. In Overview, choose a vertical and the requested number of clinics, then start **Анализ + генерация писем**. The backend processes eligible records sequentially and persists the job, per-company outcomes, counters, and activity in SQLite. It continues while the backend process remains running, including when the dashboard tab is closed. Failed company records do not stop the rest of the batch. After a restart, interrupted work is shown in the dashboard and requires an explicit resume.

Existing current analyses and drafts are not regenerated automatically. Repeating an analysis or draft is a separate, explicit action; analysis history is retained.

## Review and sending

Generated drafts appear in Review Queue. Approval records a review decision and never sends email.

Real SMTP is disabled by default. To send, the user must select the sending mode, count, and delay, review the send preview, and confirm the action. Sending is sequential and subject to batch and daily limits. Provider rejection, authentication failure, repeated SMTP errors, or rate limiting stops the job. Interrupted sends are not resumed automatically; uncertain attempts are isolated from retry, and the user must explicitly resume the remaining safe items.

`SIMULATED_SEND` is available for local checks and makes no SMTP connection. Keep credentials in a local, untracked environment file; do not commit secrets.

## Checks

```bash
node --check app/ui/review.js
python -m compileall -q app tests
python -m unittest discover -s tests -v
git diff --check
```

The project has not been declared production-ready. Verify provider limits, contact provenance, qualification decisions, drafts, and the send preview before any real sending is enabled.
