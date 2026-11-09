import hashlib
import json
import logging
import os
import secrets
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated

import anthropic
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from app.classifier import Classifier, LLMNotConfigured, LLMOutputInvalid, LLMRefused, TimeBudgetSpent, get_classifier
from app.models import IntakeEvent, IntakeResponse
from app.store import Store

logger = logging.getLogger("webhook_llm_triage")
logger.setLevel(logging.INFO)


class _StdoutHandler(logging.StreamHandler):
    """Writes to whatever sys.stdout is at emit time, so redirected or captured stdout still gets the line."""

    stream = property(lambda self: sys.stdout, lambda self, _: None)


_handler = _StdoutHandler()
_handler.setFormatter(logging.Formatter("%(message)s"))  # each message is already one JSON object
logger.addHandler(_handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = Store(Path(os.environ.get("DATA_DIR", "./data")))
    try:
        yield
    finally:
        app.state.store.close()


app = FastAPI(title="webhook-llm-triage", lifespan=lifespan)

MAX_INTAKE_BODY_BYTES = 64 * 1024  # a 20k-char message plus the other fields fits easily

# Most specific first: APITimeoutError is an APIConnectionError, and 503/529 are InternalServerErrors.
LLM_FAILURES: list[tuple[type[Exception] | tuple[type[Exception], ...], int, str]] = [
    (LLMRefused, 502, "llm_refused"),
    (LLMOutputInvalid, 502, "llm_invalid_output"),
    ((TimeBudgetSpent, anthropic.APITimeoutError), 504, "llm_timeout"),
    ((LLMNotConfigured, anthropic.AuthenticationError, anthropic.PermissionDeniedError), 500, "config_error"),
    ((anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError), 503, "llm_unavailable"),
    # Last: any other API status error (400 rejected request, 404 unknown model, ...) means our request or settings are wrong.
    (anthropic.APIStatusError, 500, "config_error"),
]


def utc_today() -> date:
    return datetime.now(UTC).date()


def max_events_per_day() -> int | None:
    """The daily cap from MAX_EVENTS_PER_DAY (default 200), or None if it is not a whole number >= 0."""
    try:
        cap = int(os.environ.get("MAX_EVENTS_PER_DAY", "200"))
    except ValueError:
        return None
    return cap if cap >= 0 else None


webhook_secret_header = APIKeyHeader(name="X-Webhook-Secret", auto_error=False)


def is_valid_webhook_secret(given: str | None) -> bool:
    expected = os.environ.get("WEBHOOK_SECRET", "")
    # Fail closed when no secret is configured; compare bytes so non-ASCII input can't raise.
    return bool(expected) and given is not None and secrets.compare_digest(given.encode(), expected.encode())


def require_webhook_secret(request: Request, given: Annotated[str | None, Depends(webhook_secret_header)]) -> None:
    if not is_valid_webhook_secret(given):
        request.state.error = "unauthorized"
        raise HTTPException(status_code=401, detail="unauthorized")


def get_store(request: Request) -> Store:
    return request.app.state.store


async def received_body(request: Request) -> bytes:
    """The request body exactly as received (cached by Starlette), extra fields and all."""
    return await request.body()


def idempotency_key(payload, header: str | None) -> str:
    """The Idempotency-Key header if sent, else the SHA-256 of the canonical JSON of the received body."""
    if header:
        return header
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


# Sent by scripts/replay_dlq.py with the dead letter's original received_at, so a replay that fails again is
# re-queued with its new reason but still ages out 14 days after it was first received (D8).
DEAD_LETTER_REPLAY_HEADER = "X-Dead-Letter-Replay"


def received_at(request: Request) -> str:
    try:
        replayed = datetime.fromisoformat(request.headers.get(DEAD_LETTER_REPLAY_HEADER, ""))
    except ValueError:
        replayed = None
    return replayed.isoformat() if replayed and replayed.tzinfo else datetime.now(UTC).isoformat()


def dead_letter(request: Request, payload, reason: str, error) -> None:
    """Save a failed intake event to the dead-letter file. The payload goes there only, never to logs."""
    request.app.state.store.add_dead_letter({
        "event_id": request.state.event_id,
        "key": idempotency_key(payload, request.headers.get("Idempotency-Key")),
        "received_at": received_at(request),
        "reason": reason,
        "error": error,
        "payload": payload,
    })


def _intake_body_size_ok(content_length: str | None) -> bool:
    return content_length is not None and content_length.isdigit() and int(content_length) <= MAX_INTAKE_BODY_BYTES


@app.middleware("http")
async def log_one_line_per_request(request: Request, call_next):
    request.state.event_id = uuid.uuid4().hex
    start = time.monotonic()
    status = 500
    try:
        if request.url.path == "/intake" and not _intake_body_size_ok(request.headers.get("content-length")):
            # Checked before the body is read, so strangers can't make us buffer and parse a huge body.
            request.state.error = "payload_too_large"
            response = JSONResponse(status_code=413, content={"error": "payload_too_large"})
        else:
            response = await call_next(request)
        status = response.status_code
        return response
    finally:
        # Only ids, status and error codes: never message text, email or secrets.
        error = getattr(request.state, "error", None) or ("internal_error" if status >= 500 else None)
        logger.info(json.dumps({
            "event_id": request.state.event_id,
            "path": request.url.path,
            "status": status,
            "latency_ms": round((time.monotonic() - start) * 1000, 1),
            "outcome": getattr(request.state, "outcome", "ok" if status < 400 else "rejected"),
            "error": error,
        }))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    # FastAPI validates the body before running the secret dependency; keep 401 first for strangers.
    if not is_valid_webhook_secret(request.headers.get("X-Webhook-Secret")):
        request.state.error = "unauthorized"
        return JSONResponse(status_code=401, content={"detail": "unauthorized"})
    errors = exc.errors()
    # exc.body: the raw text for malformed JSON, the parsed JSON otherwise (bytes or unset for odd content types).
    payload = exc.body.decode(errors="replace") if isinstance(exc.body, bytes) else exc.body
    if not isinstance(payload, (str, dict, list)):
        payload = None
    if any(e["type"] == "json_invalid" for e in errors):
        request.state.error = "malformed_json"
        dead_letter(request, payload, "malformed_json", errors[0]["ctx"]["error"])
        return JSONResponse(status_code=400, content={"error": "malformed_json"})
    request.state.error = "invalid_payload"
    # loc/msg/type only: the raw input would echo message text and email back.
    details = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in errors]
    dead_letter(request, payload, "invalid_payload", details)
    return JSONResponse(status_code=422, content={"error": "invalid_payload", "details": details})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/intake", dependencies=[Depends(require_webhook_secret)])
def intake(
    event: IntakeEvent,
    request: Request,
    classify: Annotated[Classifier, Depends(get_classifier)],
    store: Annotated[Store, Depends(get_store)],
    body: Annotated[bytes, Depends(received_body)],
    idempotency_key_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> IntakeResponse:
    payload = json.loads(body)  # already parsed once by FastAPI, so valid JSON; unlike `event` it keeps extra fields
    key = idempotency_key(payload, idempotency_key_header)
    if stored := store.processed_response(key):
        duplicate = IntakeResponse.model_validate_json(stored).model_copy(update={"status": "duplicate"})
        request.state.event_id = duplicate.event_id  # log the original event_id so both deliveries can be traced
        request.state.outcome = "duplicate"
        return duplicate
    if (cap := max_events_per_day()) is None:
        request.state.error = "config_error"
        dead_letter(request, payload, "config_error", "MAX_EVENTS_PER_DAY is not a whole number >= 0")
        return JSONResponse(status_code=500, content={"error": "config_error"})
    # Outside the try below: a daily cap 429 is not a failure, so it is never dead-lettered.
    if not store.take_daily_cap_slot(utc_today().isoformat(), cap):
        request.state.error = "daily_cap_reached"
        return JSONResponse(status_code=429, content={"error": "daily_cap_reached"})
    try:
        triage = classify(event)
    except Exception as exc:
        for failure, status_code, error in LLM_FAILURES:
            if isinstance(exc, failure):
                request.state.error = error
                dead_letter(request, payload, error, f"{type(exc).__name__}: {exc}")
                return JSONResponse(status_code=status_code, content={"error": error})
        dead_letter(request, payload, "internal_error", f"{type(exc).__name__}: {exc}")
        raise
    response = IntakeResponse(event_id=request.state.event_id, status="processed", triage=triage)
    store.save_processed(key, response.model_dump_json())
    request.state.outcome = "processed"
    return response
