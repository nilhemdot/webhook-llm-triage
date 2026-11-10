# Glossary

Shared vocabulary for code, tests, issues and docs. Use these terms exactly; avoid the listed synonyms.

| Term | Meaning | Avoid |
|---|---|---|
| **Intake event** | One request delivered to `POST /intake` by a Zap: the Google Form fields `name`, `company`, `email`, `subject`, `message`. | request, ticket, submission (in code) |
| **Triage result** | The validated classification returned for an intake event: `category`, `urgency`, `summary`. | prediction, output, label |
| **Category** | One of `quote_request`, `support_ticket`, `access_request`, `renewal_or_billing`, `vendor_or_partner`, `other`. | type, class |
| **Urgency** | `high` (outage, security issue, user blocked from working), `medium` (deadline within about a week, or a dated renewal/quote), `low` (everything else). | priority, severity |
| **Classifier** | The component that asks the LLM for a triage result, re-asks once on invalid output, and enforces the time budget. | AI step (fine in prose), model call |
| **Time budget** | The 20-second ceiling on handling one intake event, kept under Zapier's 30-second action timeout. | timeout (that's one LLM call's limit) |
| **Idempotency key** | Identifies an intake event across deliveries: the `Idempotency-Key` header if sent, else the SHA-256 of the canonical JSON body. | dedupe id, event hash |
| **Duplicate** | An intake event whose idempotency key was already processed; answered from the stored triage result without calling the LLM. | retry, repeat |
| **Dead letter** | A failed intake event saved to `dead_letter.jsonl` with its reason, so it can be replayed. Kept 14 days. | DLQ entry (fine as shorthand), error log |
| **Replay** | Re-sending dead letters to `/intake` with `scripts/replay_dlq.py`. Distinct from **Zap replay** (re-running a Zap run from Zapier's Zap History). | retry |
| **Daily cap** | The maximum number of LLM-calling intake events per UTC day (`MAX_EVENTS_PER_DAY`); beyond it, `/intake` returns 429. | rate limit, quota |
| **Webhook secret** | The shared value Zapier sends in `X-Webhook-Secret`; compared in constant time. | API key, token |
| **Data dir** | `DATA_DIR`: where SQLite and dead letters live (`./data` locally, the persistent disk `/var/data` on Render). | storage, volume |
