import json
import logging
import os
import secrets
import sys
import time
import uuid
from typing import Annotated

import anthropic
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from app.classifier import Classifier, LLMNotConfigured, LLMOutputInvalid, LLMRefused, TimeBudgetSpent, get_classifier
from app.models import IntakeEvent, IntakeResponse

logger = logging.getLogger("webhook_llm_triage")
logger.setLevel(logging.INFO)


class _StdoutHandler(logging.StreamHandler):
    """Writes to whatever sys.stdout is at emit time, so redirected or captured stdout still gets the line."""

    stream = property(lambda self: sys.stdout, lambda self, _: None)


_handler = _StdoutHandler()
_handler.setFormatter(logging.Formatter("%(message)s"))  # each message is already one JSON object
logger.addHandler(_handler)

app = FastAPI(title="webhook-llm-triage")

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

webhook_secret_header = APIKeyHeader(name="X-Webhook-Secret", auto_error=False)


def is_valid_webhook_secret(given: str | None) -> bool:
    expected = os.environ.get("WEBHOOK_SECRET", "")
    # Fail closed when no secret is configured; compare bytes so non-ASCII input can't raise.
    return bool(expected) and given is not None and secrets.compare_digest(given.encode(), expected.encode())


def require_webhook_secret(request: Request, given: Annotated[str | None, Depends(webhook_secret_header)]) -> None:
    if not is_valid_webhook_secret(given):
        request.state.error = "unauthorized"
        raise HTTPException(status_code=401, detail="unauthorized")


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
    if any(e["type"] == "json_invalid" for e in errors):
        request.state.error = "malformed_json"
        return JSONResponse(status_code=400, content={"error": "malformed_json"})
    request.state.error = "invalid_payload"
    # loc/msg/type only: the raw input would echo message text and email back.
    details = [{"loc": list(e["loc"]), "msg": e["msg"], "type": e["type"]} for e in errors]
    return JSONResponse(status_code=422, content={"error": "invalid_payload", "details": details})


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/intake", dependencies=[Depends(require_webhook_secret)])
def intake(event: IntakeEvent, request: Request, classify: Annotated[Classifier, Depends(get_classifier)]) -> IntakeResponse:
    try:
        triage = classify(event)
    except Exception as exc:
        for failure, status_code, error in LLM_FAILURES:
            if isinstance(exc, failure):
                request.state.error = error
                return JSONResponse(status_code=status_code, content={"error": error})
        raise
    request.state.outcome = "processed"
    return IntakeResponse(event_id=request.state.event_id, status="processed", triage=triage)
