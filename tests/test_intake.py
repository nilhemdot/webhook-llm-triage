import json
import logging

from conftest import WEBHOOK_SECRET

CATEGORIES = {"quote_request", "support_ticket", "access_request", "renewal_or_billing", "vendor_or_partner", "other"}
AUTH = {"X-Webhook-Secret": WEBHOOK_SECRET}


def log_lines(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "webhook_llm_triage"]


def test_healthz_returns_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_valid_intake_event_returns_processed_triage_result(client, intake_event):
    response = client.post("/intake", json=intake_event, headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "processed"
    assert body["event_id"]
    triage = body["triage"]
    assert set(triage) == {"category", "urgency", "summary"}
    assert triage["category"] in CATEGORIES
    assert triage["urgency"] in {"low", "medium", "high"}
    assert len(triage["summary"]) <= 280


def test_missing_webhook_secret_returns_401(client, intake_event):
    response = client.post("/intake", json=intake_event)
    assert response.status_code == 401


def test_wrong_webhook_secret_returns_401(client, intake_event):
    response = client.post("/intake", json=intake_event, headers={"X-Webhook-Secret": "wrong"})
    assert response.status_code == 401


def test_non_ascii_webhook_secret_returns_401_not_500(client, intake_event):
    response = client.post("/intake", json=intake_event, headers={"X-Webhook-Secret": "sécret".encode()})
    assert response.status_code == 401


def test_unset_webhook_secret_rejects_every_intake_event(client, intake_event, monkeypatch):
    monkeypatch.delenv("WEBHOOK_SECRET")
    response = client.post("/intake", json=intake_event, headers={"X-Webhook-Secret": ""})
    assert response.status_code == 401


def test_malformed_json_returns_400_malformed_json(client):
    response = client.post(
        "/intake", content=b'{"name": "Dana",', headers={**AUTH, "Content-Type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json() == {"error": "malformed_json"}


def test_malformed_json_with_wrong_webhook_secret_returns_401(client):
    response = client.post(
        "/intake", content=b"{not json", headers={"X-Webhook-Secret": "wrong", "Content-Type": "application/json"}
    )
    assert response.status_code == 401


def test_missing_message_returns_422_invalid_payload_with_details(client, intake_event):
    del intake_event["message"]
    response = client.post("/intake", json=intake_event, headers=AUTH)
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "invalid_payload"
    assert any(d["loc"][-1] == "message" for d in body["details"])


def test_oversized_subject_returns_422_invalid_payload(client, intake_event):
    intake_event["subject"] = "x" * 301
    response = client.post("/intake", json=intake_event, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_payload"
    assert any(d["loc"][-1] == "subject" for d in response.json()["details"])


def test_oversized_message_returns_422_invalid_payload(client, intake_event):
    intake_event["message"] = "x" * 20_001
    response = client.post("/intake", json=intake_event, headers=AUTH)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_payload"


def test_empty_message_returns_422_invalid_payload(client, intake_event):
    intake_event["message"] = ""
    response = client.post("/intake", json=intake_event, headers=AUTH)
    assert response.status_code == 422


def test_processed_intake_event_writes_one_json_log_line(client, intake_event, caplog):
    caplog.set_level(logging.INFO)
    response = client.post("/intake", json=intake_event, headers=AUTH)
    [line] = log_lines(caplog)
    assert set(line) == {"event_id", "path", "status", "latency_ms", "outcome", "error"}
    assert line["event_id"] == response.json()["event_id"]
    assert line["path"] == "/intake"
    assert line["status"] == 200
    assert line["outcome"] == "processed"
    assert line["error"] is None
    assert isinstance(line["latency_ms"], (int, float))


def test_rejected_requests_write_one_log_line_each_with_error(client, intake_event, caplog):
    caplog.set_level(logging.INFO)
    client.post("/intake", json=intake_event, headers={"X-Webhook-Secret": "wrong"})
    client.post("/intake", content=b"{", headers={**AUTH, "Content-Type": "application/json"})
    client.post("/intake", json={**intake_event, "message": ""}, headers=AUTH)
    lines = log_lines(caplog)
    assert [(l["status"], l["error"]) for l in lines] == [
        (401, "unauthorized"),
        (400, "malformed_json"),
        (422, "invalid_payload"),
    ]


def test_log_line_never_contains_message_text_email_or_secret(client, intake_event, caplog):
    caplog.set_level(logging.INFO)
    intake_event["message"] = "SECRET-PLANS-7731 please call me"
    client.post("/intake", json=intake_event, headers=AUTH)
    client.post("/intake", json={**intake_event, "subject": "x" * 301}, headers=AUTH)
    client.post("/intake", json=intake_event, headers={"X-Webhook-Secret": "wrong-secret-9911"})
    text = "\n".join(r.getMessage() for r in caplog.records)
    for leaked in ("SECRET-PLANS-7731", "dana@example.com", WEBHOOK_SECRET, "wrong-secret-9911"):
        assert leaked not in text
