import json

import httpx2
import pytest
from fastapi.testclient import TestClient

from app.classifier import get_classifier
from conftest import WEBHOOK_SECRET, claude_reply

AUTH = {"X-Webhook-Secret": WEBHOOK_SECRET}
RATE_LIMITED = httpx2.Response(429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}})
UNAUTHORISED = httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "bad"}})
REFUSAL = httpx2.Response(200, json=claude_reply(stop_reason="refusal"))
INVALID = httpx2.Response(200, json=claude_reply("not json"))


def dead_letters(data_dir):
    path = data_dir / "dead_letter.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.parametrize(
    "replies, status, reason",
    [
        ([RATE_LIMITED], 503, "llm_unavailable"),
        ([httpx2.ReadTimeout("fake timeout")], 504, "llm_timeout"),
        ([REFUSAL], 502, "llm_refused"),
        ([INVALID, INVALID], 502, "llm_invalid_output"),
        ([UNAUTHORISED], 500, "config_error"),
    ],
)
def test_llm_failure_appends_one_dead_letter_with_reason_and_full_payload(
    client, fake_claude, intake_event, data_dir, replies, status, reason
):
    fake_claude.queue(*replies)
    response = client.post("/intake", json=intake_event, headers={**AUTH, "Idempotency-Key": "form-response-1"})
    assert response.status_code == status
    [letter] = dead_letters(data_dir)
    assert letter["reason"] == reason
    assert letter["error"]
    assert letter["payload"] == intake_event
    assert letter["key"] == "form-response-1"
    assert letter["event_id"] and letter["received_at"]


def test_malformed_json_appends_dead_letter_with_raw_body(client, data_dir):
    response = client.post("/intake", content=b'{"name": "Dana', headers={**AUTH, "Content-Type": "application/json"})
    assert response.status_code == 400
    [letter] = dead_letters(data_dir)
    assert letter["reason"] == "malformed_json"
    assert letter["payload"] == '{"name": "Dana'


def test_invalid_payload_appends_dead_letter_with_the_payload(client, intake_event, data_dir):
    event = {**intake_event, "message": ""}
    response = client.post("/intake", json=event, headers=AUTH)
    assert response.status_code == 422
    [letter] = dead_letters(data_dir)
    assert letter["reason"] == "invalid_payload"
    assert letter["payload"] == event


def test_wrong_webhook_secret_does_not_dead_letter(client, intake_event, data_dir):
    assert client.post("/intake", json=intake_event, headers={"X-Webhook-Secret": "wrong"}).status_code == 401
    assert client.post("/intake", content=b"{", headers={"X-Webhook-Secret": "wrong"}).status_code == 401
    assert dead_letters(data_dir) == []


def test_success_and_duplicate_do_not_dead_letter(client, intake_event, data_dir):
    client.post("/intake", json=intake_event, headers=AUTH)
    client.post("/intake", json=intake_event, headers=AUTH)
    assert dead_letters(data_dir) == []


def test_dead_letter_payload_is_never_logged(client, fake_claude, intake_event, data_dir, caplog, capsys):
    caplog.set_level("INFO")
    fake_claude.queue(RATE_LIMITED)
    client.post("/intake", json=intake_event, headers=AUTH)
    assert dead_letters(data_dir)  # the payload went to the dead-letter file
    logged = caplog.text + capsys.readouterr().out
    assert intake_event["email"] not in logged
    assert intake_event["message"] not in logged


def test_llm_failure_dead_letter_keeps_the_raw_body_with_extra_fields(client, fake_claude, intake_event, data_dir):
    event = {**intake_event, "form_response_id": "abc-123"}
    fake_claude.queue(RATE_LIMITED)
    assert client.post("/intake", json=event, headers=AUTH).status_code == 503
    [letter] = dead_letters(data_dir)
    assert letter["payload"] == event


def test_unexpected_exception_after_auth_appends_internal_error_dead_letter_with_raw_body(
    configured_app, intake_event, data_dir
):
    def broken_classifier(event):
        raise RuntimeError("bug")

    configured_app.dependency_overrides[get_classifier] = lambda: broken_classifier
    event = {**intake_event, "form_response_id": "abc-123"}
    with TestClient(configured_app, raise_server_exceptions=False) as client:
        assert client.post("/intake", json=event, headers=AUTH).status_code == 500
    [letter] = dead_letters(data_dir)
    assert letter["reason"] == "internal_error"
    assert letter["error"] == "RuntimeError: bug"
    assert letter["payload"] == event
