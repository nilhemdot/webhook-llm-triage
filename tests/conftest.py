import json

import anthropic
import httpx2
import pytest
from fastapi.testclient import TestClient

from app.classifier import get_anthropic_client
from app.main import app

WEBHOOK_SECRET = "test-webhook-secret"
VALID_TRIAGE_RESULT = {"category": "quote_request", "urgency": "medium", "summary": "Quote for 20 laptops by Friday."}


def claude_reply(text=None, stop_reason="end_turn", content=None):
    """Messages API JSON for one fake Claude reply."""
    if content is None:
        content = [] if text is None else [{"type": "text", "text": text}]
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": content,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 10},
    }


class FakeClaude:
    """Claude faked at its HTTP transport: queued replies are served in order, requests are recorded."""

    def __init__(self):
        self.replies = []  # each: an httpx2.Response, an exception to raise, or a callable(request) -> Response
        self.requests = []

    def queue(self, *replies):
        self.replies.extend(replies)

    def handler(self, request):
        self.requests.append(request)
        reply = self.replies.pop(0) if self.replies else httpx2.Response(200, json=claude_reply(json.dumps(VALID_TRIAGE_RESULT)))
        if isinstance(reply, Exception):
            raise reply
        return reply(request) if callable(reply) else reply

    @property
    def calls(self):
        return len(self.requests)

    def body(self, n=0):
        return json.loads(self.requests[n].content)


@pytest.fixture
def fake_claude():
    return FakeClaude()


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def configured_app(monkeypatch, fake_claude, data_dir):
    """The app with test settings and the fake Claude; enter `TestClient(configured_app)` to start it."""
    monkeypatch.setenv("WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    claude = anthropic.Anthropic(
        api_key="test", max_retries=0, http_client=httpx2.Client(transport=httpx2.MockTransport(fake_claude.handler))
    )
    app.dependency_overrides[get_anthropic_client] = lambda: claude
    yield app
    app.dependency_overrides.clear()


@pytest.fixture
def client(configured_app):
    with TestClient(configured_app) as client:
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
