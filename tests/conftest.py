import pytest
from fastapi.testclient import TestClient

from app.main import app

WEBHOOK_SECRET = "test-webhook-secret"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def intake_event():
    return {
        "name": "Dana Example",
        "company": "Example Corp",
        "email": "dana@example.com",
        "subject": "Need a quote for 20 laptops",
        "message": "Please send pricing for 20 business laptops by next Friday.",
    }
