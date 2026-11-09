# Failure trace

One real failure, caused on purpose against the deployed service and followed from symptom to fix. Every `[X]` is filled in from that run; nothing here is invented. Status codes are explained in the [failure-mode table](../README.md#failure-modes) and recovery steps in the [RUNBOOK](../RUNBOOK.md).

## What was done

- **Date / time (UTC):** [X]
- **Failure caused:** [X] (for example: a form submitted during a redeploy, or `ANTHROPIC_MODEL` set to a model that doesn't exist)
- **Commit deployed:** [X]

## Symptom

- **Zap History:** run status [X], Webhooks step response [X]
- **Zap errors sheet (Zap B):** row added at [X] with [X]
- Screenshot: [X]

## Log line

```json
[X]
```

## Root cause

[X]

## Fix or workaround

[X]

## Recovery

- **Replay used:** [X] (Zap replay or `scripts/replay_dlq.py`)
- **Result:** [X] (status, triage result, sheet row)

## What would catch it sooner

[X]
