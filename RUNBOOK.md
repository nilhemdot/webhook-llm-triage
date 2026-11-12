# Runbook

For whoever handles an incident with the triage service. Status codes and their meaning are in the [failure-mode table](README.md#failure-modes); terms are in [GLOSSARY.md](GLOSSARY.md).

## Where to look

- **Zap History** (Zapier): every Zap A run with its status and the step's response.
- **Zap errors sheet**: one row per Zap error, written by Zap B (Zapier Manager → New Zap Error). It has no per-step detail; open the run in Zap History for that.
- **Render logs** (service → Logs): one JSON line per request: `event_id`, `path`, `status`, `latency_ms`, `outcome`, `error`. Search by `event_id` or `error`. Logs never contain message text, email addresses or secrets.
- **Dead letters**: `/var/data/dead_letter.jsonl` on the Render disk (Render service → Shell). Each line holds the event id, idempotency key, reason, error and full payload. Treat it as personal data: don't paste it into tickets or chat.
- **Health**: `curl -s https://<app>.onrender.com/healthz` returns `{"ok":true}`.

## Zap run statuses

| Zap run status | What it means here | What to do |
|---|---|---|
| Successful | `/intake` returned 200 and the sheet row was written. `status: "duplicate"` is also a 200. | Nothing. |
| Errored | A step failed. For the Webhooks step, `/intake` returned a non-2xx status (or didn't answer within 30 s). | Read the status and `error` in the step's response, find it in the [failure-mode table](README.md#failure-modes), fix the cause, then recover with Zap replay or dead-letter replay (below). |
| Safely halted | A step stopped the run on purpose (e.g. a Filter). | Nothing, unless the halt was unexpected. |
| Filtered | A Filter step stopped the run. | Nothing. |
| On hold | Zapier paused the run (e.g. a Zap or account problem). | Fix what Zapier reports, then release or replay the run. |
| Scheduled | Zapier will run it later. | Wait. |

## Zap replay

Use it when the service never processed the intake event: 401, 413, 429, a deploy gap, or a 400/422 caused by the Zap's own mapping. It works on Free for errored runs within 60 days.

1. Fix the cause first (wrong secret, mapping, cap, configuration). A replay with the same fault fails the same way.
2. Zap History → open the errored run → **Replay**.
3. The replay sends the same form response ID as `Idempotency-Key`, so if the service did process the event after all, it answers `duplicate` with the stored triage result and calls Claude once at most.

## Dead-letter replay

Use it for intake events the service saved after a failure: 500, 502, 503 and 504. Do not use it for 400/422 dead letters caused by a broken Zap mapping: those payloads fail validation again; fix the Zap and use Zap replay instead.

The dead letters are on the Render disk, so run the tool from the Render service's **Shell**, where `DATA_DIR` is already `/var/data` and `WEBHOOK_SECRET` is already set. The tool reads the secret from that environment variable only; never type it on the command line.

```sh
uv run --no-dev python scripts/replay_dlq.py --url https://<app>.onrender.com --dry-run
uv run --no-dev python scripts/replay_dlq.py --url https://<app>.onrender.com
```

- `--dry-run` lists each dead letter's event id, received time and reason, and marks those older than 14 days. It sends and changes nothing.
- A real run re-sends each dead letter with its original `Idempotency-Key`, moves successes (200, including `duplicate`) to `dead_letter.replayed.jsonl`, keeps failures in `dead_letter.jsonl` (with the new failure's reason and error when the service rejected the replay), and deletes entries older than 14 days from both files. It exits 1 if any remain.
- A replayed event counts toward the daily cap like any other LLM-calling intake event.
- The service answers the replay but Zapier never sees it: Zap A doesn't write a sheet row for it. Add the row by hand from the replay's result, or use Zap replay instead when the run is still in Zap History.
- If a run is interrupted, `dead_letter.replaying.jsonl` is left behind; the next run picks it up first.

## Rotate the webhook secret

There is one secret at a time, so intake events sent between steps 2 and 3 get 401; recover them with Zap replay.

1. Generate a new value: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`.
2. Render → service → Environment → set `WEBHOOK_SECRET` → save (the service redeploys).
3. Zap A → Webhooks step → Headers → set `X-Webhook-Secret` to the new value → publish.
4. Submit one test form entry and check the run is Successful. Zap replay any 401 runs from the gap.

## Rotate the Anthropic API key

1. Anthropic Console → the workspace with the spend limit → create a new API key.
2. Render → service → Environment → set `ANTHROPIC_API_KEY` → save (the service redeploys).
3. Submit one test form entry and check the run is Successful.
4. Console → revoke the old key. If a 500 `config_error` appears afterwards, the new key wasn't picked up: check step 2, then replay dead letters.

Rotate both right away if a secret may have leaked; a leaked webhook secret can spend up to the daily cap per day until it is rotated.

## Zapier plan

Zap A ("Support intake triage") needs a paid Zapier plan: it has two actions, and its Webhooks step is a premium app. The Professional trial ends on 2026-11-22 and the account drops to Free unless someone upgrades. After that:

- Zapier turns Zap A off, because Free allows one action per Zap. New form responses no longer reach `/intake`.
- Runs that reach the Webhooks step are put On hold, not Errored. Zapier keeps their trigger data.
- After an upgrade, turn Zap A back on, then replay the held runs from Zap History.

Zap B (Zapier Manager → Sheets) has one action and no premium app, so it keeps running on Free. The Render service is unaffected.

## Deploys

Each deploy has a short gap (a service with a disk gets no zero-downtime deploys). Zap A runs in the gap show Errored; Zap replay them after the deploy is live.

## Escalation

| Problem | Escalate to |
|---|---|
| Configuration, secrets, Render service, cost | Service owner: [X] |
| Zap A / Zap B behaviour, Zapier plan | Zap builder: [X] |
| Claude API errors that persist (503/504 for more than [X] minutes) | Check status.anthropic.com, then the service owner |
| A bug (500 `internal_error`) | Open a GitHub issue with the `event_id` and log line (no payload) |
