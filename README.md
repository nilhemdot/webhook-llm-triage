# webhook-llm-triage

[![CI](https://github.com/nilhemdot/webhook-llm-triage/actions/workflows/ci.yml/badge.svg)](https://github.com/nilhemdot/webhook-llm-triage/actions/workflows/ci.yml)

Zapier webhook to LLM triage service with retries, dedupe and a dead-letter queue. Work in progress.

## Quick start

```sh
uv sync
uv run pytest -q
WEBHOOK_SECRET=change-me uv run uvicorn app.main:app --no-access-log
```

## Smoke test against the real Claude API

The test suite fakes Claude. To make one real call (well under a cent) with the configured model, structured output and server-side fallbacks:

```sh
ant auth login            # or export ANTHROPIC_API_KEY=...
uv run python scripts/smoke.py
```

It prints the model and the triage result for a sample intake event. If the API rejects the `server-side-fallback-2026-07-01` beta, drop `betas`/`fallbacks` in `app/classifier.py` and note it here.

## Dead letters and replay

Every intake event that fails after the webhook secret check (400, 422, 5xx) is appended to `DATA_DIR/dead_letter.jsonl` with its event id, idempotency key, received time, reason, error and **full payload**. The payload is kept so the event can be replayed; it goes to that file only, never to the logs. Dead letters and replayed entries are deleted after 14 days by the replay command.

```sh
DATA_DIR=/var/data uv run python scripts/replay_dlq.py --url https://<app>.onrender.com --secret "$WEBHOOK_SECRET" --dry-run  # list only
DATA_DIR=/var/data uv run python scripts/replay_dlq.py --url https://<app>.onrender.com --secret "$WEBHOOK_SECRET"
```

A real run re-sends each dead letter with its original `Idempotency-Key`, moves successes to `dead_letter.replayed.jsonl`, keeps failures (with their original received time, so they still age out after 14 days) and exits 1 if any remain. Replay requests carry `X-Dead-Letter-Replay: 1` so the service doesn't dead-letter them a second time.
