# Security

How the triage service protects its secrets, the personal data in intake events, and the Claude bill. To report a vulnerability, open a GitHub issue without exploit details and ask for a private contact.

## Synthetic data only

This is a demo. The Google Form is labelled "demo, do not enter real data", and every test, UAT row and screenshot uses made-up names, companies and emails. Don't send real customer data through it.

## Secrets live in the environment only

- `WEBHOOK_SECRET` and `ANTHROPIC_API_KEY` are read from the environment. Nothing secret is in the repo: `.env` is gitignored, [.env.example](.env.example) has empty values, and [render.yaml](render.yaml) declares both with `sync: false`, so Render prompts for them in the dashboard.
- On a developer machine the Anthropic SDK uses the `ant auth login` sign-in, so no API key sits in a file. On Render it uses an API key, because interactive OAuth is meant for development machines, not servers.
- Rotation steps are in [RUNBOOK.md](RUNBOOK.md).

## Webhook secret check

- Every `POST /intake` must carry `X-Webhook-Secret`. It is compared with `secrets.compare_digest` (constant time, on bytes), so it can't be guessed through response timing.
- Missing or wrong → always 401, never 400 or 422, so strangers learn nothing about the payload format. If `WEBHOOK_SECRET` is unset or empty, every request is refused (fail closed).
- Bodies over 64 KB (or without `Content-Length`) are refused with 413 before they are read.
- The secret goes in a header, never in the URL.
- Limitation: a shared secret, not HMAC request signing, so a leaked secret is valid until rotated.

## Personal data

- **Logs:** one JSON line per request with only `event_id`, `path`, `status`, `latency_ms`, `outcome` and `error` codes. Message text, names, email addresses and secrets are never logged. A 422 response lists field locations and messages, not the input values.
- **Claude:** only `company`, `subject` and `message` are sent; `name` and `email` never leave the service.
- **Dead letters:** `dead_letter.jsonl` on the persistent disk holds the **full payload**, so a failed intake event can be replayed. Entries older than 14 days are deleted from it (and from `dead_letter.replayed.jsonl`) when `scripts/replay_dlq.py` runs; the tool prints ids and reasons, never payloads. The purge isn't automatic: run the tool at least every 14 days while the service is live.
- **Processed intake events:** SQLite on the disk keeps each idempotency key with its response (category, urgency, summary) so duplicates can be answered. Rows older than 14 days are deleted automatically (at startup and at most once an hour on later writes), so duplicate detection covers 14 days: a redelivery after that is processed as a new intake event.

## Cost controls

- **Daily cap:** `MAX_EVENTS_PER_DAY` (default 200) LLM-calling intake events per UTC day; beyond it `/intake` returns 429 without calling Claude. Duplicates and rejected requests don't count. The count is in SQLite, so it survives restarts and deploys.
- **Spend limit:** the production API key belongs to an Anthropic Console workspace with a monthly spend limit, which caps the bill even if the daily cap is raised or bypassed.
- Each intake event gets at most one re-ask (the SDK may also retry the first call once on a rate limit, 5xx or timeout), all within a 20-second time budget.

## Prompt injection

The message text comes from an anonymous web form, so it is treated as untrusted data:

- The system prompt says the intake event is data, not instructions, and must be classified by the written category and urgency rules whatever it asks for.
- The fields are sent as JSON inside an `<intake_event>` wrapper, with `<` and `>` escaped so the text can't close the wrapper.
- Claude's reply is constrained to a JSON schema and validated: category and urgency must be one of the allowed values, the summary at most 280 characters. Anything else is re-asked once, then fails as 502 `llm_invalid_output`; it never reaches the sheet.
- Limitation: injection can still steer the choice *within* the allowed values or the wording of the summary. The UAT includes a prompt-injection submission to check this ([docs/UAT.md](docs/UAT.md)).

## Spreadsheet formula injection

Zap A writes the summary into a Google Sheets cell. A summary starting with `=`, `+`, `-`, `@`, a tab or a carriage return is prefixed with `'` so Sheets shows it as text instead of running it as a formula.
