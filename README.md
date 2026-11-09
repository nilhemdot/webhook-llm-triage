# webhook-llm-triage

[![CI](https://github.com/nilhemdot/webhook-llm-triage/actions/workflows/ci.yml/badge.svg)](https://github.com/nilhemdot/webhook-llm-triage/actions/workflows/ci.yml)

Zapier webhook to LLM triage service with retries, dedupe and a dead-letter queue. Work in progress.

## Quick start

```sh
uv sync
uv run pytest -q
WEBHOOK_SECRET=change-me uv run uvicorn app.main:app --no-access-log
```
