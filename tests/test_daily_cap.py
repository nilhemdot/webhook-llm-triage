from datetime import date, timedelta

from fastapi.testclient import TestClient

from conftest import WEBHOOK_SECRET
from test_dead_letters import dead_letters

AUTH = {"X-Webhook-Secret": WEBHOOK_SECRET}


def new_intake_event(intake_event, n):
    return {**intake_event, "message": f"Intake event number {n}."}


def test_daily_cap_of_2_answers_429_to_the_third_new_intake_event_without_calling_claude_or_dead_lettering(
    client, fake_claude, intake_event, data_dir, monkeypatch
):
    monkeypatch.setenv("MAX_EVENTS_PER_DAY", "2")
    for n in (1, 2):
        assert client.post("/intake", json=new_intake_event(intake_event, n), headers=AUTH).status_code == 200
    response = client.post("/intake", json=new_intake_event(intake_event, 3), headers=AUTH)
    assert response.status_code == 429
    assert response.json() == {"error": "daily_cap_reached"}
    assert fake_claude.calls == 2
    assert dead_letters(data_dir) == []


def test_duplicate_still_gets_200_at_the_daily_cap(client, fake_claude, intake_event, monkeypatch):
    monkeypatch.setenv("MAX_EVENTS_PER_DAY", "2")
    for n in (1, 2):
        client.post("/intake", json=new_intake_event(intake_event, n), headers=AUTH)
    response = client.post("/intake", json=new_intake_event(intake_event, 1), headers=AUTH)
    assert response.status_code == 200
    assert response.json()["status"] == "duplicate"
    assert fake_claude.calls == 2


def test_rejected_requests_do_not_count_towards_the_daily_cap(client, intake_event, monkeypatch):
    monkeypatch.setenv("MAX_EVENTS_PER_DAY", "1")
    assert client.post("/intake", json=intake_event, headers={"X-Webhook-Secret": "wrong"}).status_code == 401
    assert client.post("/intake", json={**intake_event, "message": ""}, headers=AUTH).status_code == 422
    assert client.post("/intake", json=intake_event, headers=AUTH).status_code == 200


def test_daily_cap_count_survives_an_app_restart(configured_app, intake_event, monkeypatch):
    monkeypatch.setenv("MAX_EVENTS_PER_DAY", "1")
    with TestClient(configured_app) as client:
        assert client.post("/intake", json=new_intake_event(intake_event, 1), headers=AUTH).status_code == 200
    with TestClient(configured_app) as restarted:
        assert restarted.post("/intake", json=new_intake_event(intake_event, 2), headers=AUTH).status_code == 429


def test_daily_cap_resets_on_a_new_utc_day(client, fake_claude, intake_event, monkeypatch):
    monkeypatch.setenv("MAX_EVENTS_PER_DAY", "1")
    today = date(2026, 11, 9)
    monkeypatch.setattr("app.main.utc_today", lambda: today)
    assert client.post("/intake", json=new_intake_event(intake_event, 1), headers=AUTH).status_code == 200
    assert client.post("/intake", json=new_intake_event(intake_event, 2), headers=AUTH).status_code == 429
    monkeypatch.setattr("app.main.utc_today", lambda: today + timedelta(days=1))
    assert client.post("/intake", json=new_intake_event(intake_event, 2), headers=AUTH).status_code == 200
    assert fake_claude.calls == 2


def test_daily_cap_defaults_to_200(client, fake_claude, intake_event, monkeypatch):
    monkeypatch.delenv("MAX_EVENTS_PER_DAY", raising=False)
    for n in range(201):
        client.post("/intake", json=new_intake_event(intake_event, n), headers=AUTH)
    assert fake_claude.calls == 200
