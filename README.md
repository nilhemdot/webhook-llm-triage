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
