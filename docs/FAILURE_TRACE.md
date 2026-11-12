# Failure trace

One real failure, caused on purpose against the deployed service and followed from symptom to fix. Everything here comes from that run. Status codes are explained in the [failure-mode table](../README.md#failure-modes) and recovery steps in the [RUNBOOK](../RUNBOOK.md).

## What was done

- **Date / time (UTC):** 2026-11-10 23:56:47 (form submitted) → 23:57:10 (service rejected it) → 23:57:16 (Zap B logged it)
- **Failure caused:** a real Google Form submission whose Message was 20,001 characters (UAT row 7). The Form had no length limit, so Zap A posted it to the service.
- **Commit deployed:** `e36fb4b`

## Symptom

- **Zap History:** Zap A run `01bdd0f9-870e-a7fd-d1c8-6e015e1285f4` has status **Errored**. Its Webhooks step error reads: The app returned "String should have at most 20000 characters". No Triage row was written.
- **Zap errors sheet (Zap B):** a row was added at 2026-11-10 23:57:16 with `zap_id` `01bdd0f9-870e-a7fd-d1c8-6e015e1285f4` and that same message. `zap_name` and `step` came through empty, because Zap A is still named "Untitled Zap" and Zapier didn't fill in the step title.
- Screenshots: [Zap A run](img/zap-a-run-error.jpg), [Zap errors sheet](img/zap-b-errors-sheet.jpg), [Render logs](img/render-logs.jpg)

## Log line

```json
{"event_id": "2e8400db0cf24609a33dd2b8f7b984fd", "path": "/intake", "status": 422, "latency_ms": 4.0, "outcome": "rejected", "error": "invalid_payload"}
```

## Root cause

The service caps `message` at 20,000 characters (`IntakeEvent.message`, `max_length=20_000`) and correctly returned 422 `invalid_payload`. It also wrote the body to the dead-letter store. The gap was upstream: the Google Form accepted any length, so a submitter could send something the service will always reject. Zapier then counts that as a Zap error, not as a bad submission.

## Fix or workaround

Added response validation to the Form's Message question: **Length → Maximum character count 20000**, with the error text "Please keep the message under 20,000 characters." ([screenshot](img/form-message-validation.jpg)). The service needed no change; its limit is the same.

## Recovery

- **Replay used:** none. A 422 is permanent: replaying the same body from Zap History or with `scripts/replay_dlq.py` would get the same 422. The submitter has to resend a shorter message.
- **Result:** after the fix, re-submitting the 20,001-character message to the Form returns HTTP 400 from Google Forms. No response is recorded, Zap A doesn't run, and no Claude call or daily-cap slot is used. `/healthz` stayed at 200 the whole time.

## What would catch it sooner

A Form-to-service limits check in the UAT checklist: every server-side `max_length` in `app/models.py` should have a matching Form validation rule. Zap B already surfaces this failure within seconds; adding a Zapier notification (email/Slack) to Zap B would get it to a person, not just a Sheet.
