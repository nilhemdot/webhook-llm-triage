import json
import time

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.store import Store
from conftest import VALID_TRIAGE_RESULT, WEBHOOK_SECRET, claude_reply

AUTH = {"X-Webhook-Secret": WEBHOOK_SECRET}


def test_same_body_twice_returns_duplicate_with_original_triage_result_and_calls_claude_once(
    client, fake_claude, intake_event
):
    first = client.post("/intake", json=intake_event, headers=AUTH)
    second = client.post("/intake", json=intake_event, headers=AUTH)
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == "processed"
    assert second.json()["status"] == "duplicate"
    assert second.json()["triage"] == first.json()["triage"]
    assert second.json()["event_id"] == first.json()["event_id"]
    assert fake_claude.calls == 1


def test_idempotency_key_body_hash_ignores_key_order_and_whitespace(client, fake_claude, intake_event):
    client.post("/intake", json=intake_event, headers=AUTH)
    reordered = json.dumps(dict(reversed(intake_event.items())), indent=4).encode()
    response = client.post("/intake", content=reordered, headers={**AUTH, "Content-Type": "application/json"})
    assert response.json()["status"] == "duplicate"
    assert fake_claude.calls == 1


def test_different_bodies_without_idempotency_key_are_each_processed(client, fake_claude, intake_event):
    client.post("/intake", json=intake_event, headers=AUTH)
    response = client.post("/intake", json={**intake_event, "message": "A different message."}, headers=AUTH)
    assert response.json()["status"] == "processed"
    assert fake_claude.calls == 2


def test_same_idempotency_key_header_is_a_duplicate_even_if_body_differs(client, fake_claude, intake_event):
    client.post("/intake", json=intake_event, headers={**AUTH, "Idempotency-Key": "form-response-1"})
    response = client.post(
        "/intake", json={**intake_event, "message": "Edited."}, headers={**AUTH, "Idempotency-Key": "form-response-1"}
    )
    assert response.json()["status"] == "duplicate"
    assert fake_claude.calls == 1


def test_different_idempotency_key_headers_with_same_body_are_each_processed(client, fake_claude, intake_event):
    client.post("/intake", json=intake_event, headers={**AUTH, "Idempotency-Key": "form-response-1"})
    response = client.post("/intake", json=intake_event, headers={**AUTH, "Idempotency-Key": "form-response-2"})
    assert response.json()["status"] == "processed"
    assert fake_claude.calls == 2


def test_failed_intake_event_is_not_stored_so_a_redelivery_calls_claude_again(client, fake_claude, intake_event):
    fake_claude.queue(httpx2.Response(429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}}))
    assert client.post("/intake", json=intake_event, headers=AUTH).status_code == 503
    response = client.post("/intake", json=intake_event, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "processed"
    assert fake_claude.calls == 2


def test_duplicate_is_recognised_after_an_app_restart_with_the_same_data_dir(
    configured_app, fake_claude, intake_event, data_dir
):
    with TestClient(configured_app) as client:
        first = client.post("/intake", json=intake_event, headers=AUTH)
    assert any(data_dir.iterdir())  # the store lives in the data dir
    with TestClient(configured_app) as restarted:
        second = restarted.post("/intake", json=intake_event, headers=AUTH)
    assert second.json()["status"] == "duplicate"
    assert second.json()["triage"] == first.json()["triage"]
    assert fake_claude.calls == 1


def test_duplicate_writes_log_line_with_outcome_duplicate(client, intake_event, caplog):
    client.post("/intake", json=intake_event, headers=AUTH)
    caplog.clear()
    caplog.set_level("INFO")
    response = client.post("/intake", json=intake_event, headers=AUTH)
    [line] = [json.loads(r.getMessage()) for r in caplog.records if r.name == "webhook_llm_triage"]
    assert line["outcome"] == "duplicate"
    assert line["event_id"] == response.json()["event_id"]


# Zap A writes the summary into a Google Sheets cell; these leading characters would make it a formula.
@pytest.mark.parametrize("start", ["=", "+", "-", "@", "\t", "\r"])
def test_summary_starting_with_formula_character_is_prefixed_with_apostrophe(client, fake_claude, intake_event, start):
    summary = f"{start}HYPERLINK(\"http://evil.example\",\"click\")"
    fake_claude.queue(httpx2.Response(200, json=claude_reply(json.dumps({**VALID_TRIAGE_RESULT, "summary": summary}))))
    response = client.post("/intake", json=intake_event, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["triage"]["summary"] == "'" + summary


def test_280_char_summary_starting_with_equals_is_prefixed_and_kept_within_280(client, fake_claude, intake_event):
    summary = "=" + "x" * 279
    fake_claude.queue(httpx2.Response(200, json=claude_reply(json.dumps({**VALID_TRIAGE_RESULT, "summary": summary}))))
    response = client.post("/intake", json=intake_event, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["triage"]["summary"] == "'" + summary[:279]


def test_duplicate_of_a_formula_summary_comes_back_prefixed_once(client, fake_claude, intake_event):
    fake_claude.queue(httpx2.Response(200, json=claude_reply(json.dumps({**VALID_TRIAGE_RESULT, "summary": "=1+1"}))))
    client.post("/intake", json=intake_event, headers=AUTH)
    response = client.post("/intake", json=intake_event, headers=AUTH)
    assert response.json()["status"] == "duplicate"
    assert response.json()["triage"]["summary"] == "'=1+1"


def test_bodies_differing_only_in_an_extra_unknown_field_are_not_duplicates(client, fake_claude, intake_event):
    client.post("/intake", json=intake_event, headers=AUTH)
    response = client.post("/intake", json={**intake_event, "form_response_id": "abc-123"}, headers=AUTH)
    assert response.json()["status"] == "processed"
    assert fake_claude.calls == 2


DAY_S = 24 * 3600


def stored_response(event_id="old"):
    return json.dumps({"event_id": event_id, "status": "processed", "triage": VALID_TRIAGE_RESULT})


def test_processed_intake_events_older_than_14_days_are_purged_at_startup(
    configured_app, fake_claude, intake_event, data_dir
):
    store = Store(data_dir)
    store.save_processed("expired-key", stored_response())
    store.save_processed("fresh-key", stored_response())
    store._db.execute("UPDATE events SET created_at = ? WHERE key = 'expired-key'", (time.time() - 15 * DAY_S,))
    store._db.execute("UPDATE events SET created_at = ? WHERE key = 'fresh-key'", (time.time() - 13 * DAY_S,))
    store.close()
    with TestClient(configured_app) as client:
        expired = client.post("/intake", json=intake_event, headers={**AUTH, "Idempotency-Key": "expired-key"})
        fresh = client.post("/intake", json=intake_event, headers={**AUTH, "Idempotency-Key": "fresh-key"})
    assert expired.json()["status"] == "processed"
    assert fresh.json()["status"] == "duplicate"
    assert fake_claude.calls == 1


def test_expired_processed_intake_events_are_purged_on_a_later_write_without_a_restart(
    client, fake_claude, intake_event, monkeypatch
):
    client.post("/intake", json=intake_event, headers={**AUTH, "Idempotency-Key": "first"})
    later = time.time() + 15 * DAY_S
    monkeypatch.setattr("app.store.time.time", lambda: later)
    client.post("/intake", json=intake_event, headers={**AUTH, "Idempotency-Key": "second"})
    response = client.post("/intake", json=intake_event, headers={**AUTH, "Idempotency-Key": "first"})
    assert response.json()["status"] == "processed"
    assert fake_claude.calls == 3
