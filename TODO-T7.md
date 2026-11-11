# TODO-T7: finish T7 (#8)

Remaining steps to take webhook-llm-triage from merged code to a live, Zapier-wired, UAT-checked service.
Owner: **agent** (can be automated) or **human** (needs a sign-in, a click in a UI, or billing).

## Status

| # | Step (#8 checkbox) | Owner | Status | Check |
|---|---|---|---|---|
| 1 | Sign in with `ant auth login`, run `scripts/smoke.py` | human + agent | done 2026-11-10 | `model=claude-sonnet-5-5 {"category":"support_ticket","urgency":"high",...}` |
| 2 | Server API key in a workspace with a monthly spend limit | human | key in use; **spend limit unverified** | Console → Workspaces → Default → Limits |
| 3 | Render Blueprint applied, secrets set, `/healthz` live | agent | done 2026-11-10 | `curl https://webhook-llm-triage-pyhs.onrender.com/healthz` → 200; unsigned `/intake` → 401; signed → 200 `processed` |
| 4 | Google Form "demo, do not enter real data" (name, company, email, subject, message), **linked to a Sheet** | agent via browser | done 2026-11-10 | Form linked to "Support intake (Responses)"; Message capped at 20,000 chars |
| 5 | Zap A: Forms New Form Response → Webhooks POST (`X-Webhook-Secret`, `Idempotency-Key` = response ID) → Sheets Create Spreadsheet Row | agent via browser | done 2026-11-10 | UAT rows 1–5, 8–10 on the Triage tab |
| 5b | Zap B: Zapier Manager New Zap Error → Sheets row | agent via browser | done 2026-11-10 | Zap errors row for run `01bdd0f9…` |
| 6 | UAT table filled; one real failure traced; screenshots in `docs/img/` | agent | done 2026-11-10 | `tests/test_docs.py` asserts no `[X]` left |
| 7 | Tick #8 boxes with evidence; close #8, then #1 | agent | open | `gh issue view 8` closed |

## Current-docs check (2026-11-10)

Most of PLAN.md still matches the docs. These differ:

- **Zapier MCP can't build Zaps.** It runs single actions only. Building Zaps needs "Next Gen Zaps" early access (Pro+), or the Zap editor UI. [Zapier help](https://help.zapier.com/hc/en-us/articles/48391476448141), [Zapier MCP docs](https://docs.zapier.com/mcp/overview/how-tools-work)
- **No Zapier action creates a Google Form.** The Google Forms app has triggers plus "API Request (Beta)" only, so the form is made in the Forms UI. [Zapier: Google Forms](https://help.zapier.com/hc/en-us/articles/8495997064845)
- **The form must be linked to a Google Sheet** for "New Form Response" to fire. This was not in PLAN.md. (Same source.)
- **The Webhooks plan rules conflict in Zapier's own docs.** One page says it works on the 14-day trial, others say Pro+ or Enterprise only. Check in the UI. [Trial](https://help.zapier.com/hc/en-us/articles/8496197192461), [Webhooks](https://help.zapier.com/hc/en-us/articles/8496326446989)
- **Fallbacks on Sonnet 5.5** retry only `cyber` and `frontier_llm` refusals. Other refusal categories still end as `stop_reason: "refusal"`, which maps to 502 `llm_refused`, so the code is right. The fallback just covers less than D13 suggests. [Refusals and fallback](https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback)
- **Structured output:** the SDK strips `minLength`/`maxLength`-type limits before sending and checks them client-side after the reply. [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)

Confirmed unchanged: Render `disk` block, single-instance and no-zero-downtime limits, `sync: false`, uv and `.python-version` detection, `healthCheckPath`; Zapier Webhooks headers and JSON payload, Sheets Create Spreadsheet Row, Zapier Manager New Zap Error (Free plan included); `messages.parse(output_format=...)`; API version 2026-10-21 dropping `temperature`/`top_p`/`top_k`.
