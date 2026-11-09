import json
import logging
import os
import secrets
import sys
import time
import uuid
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from app.classifier import Classifier, get_classifier
from app.models import IntakeEvent, IntakeResponse

logger = logging.getLogger("webhook_llm_triage")
logger.setLevel(logging.INFO)
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))  # each message is already one JSON object
logger.addHandler(_handler)

app = FastAPI(title="webhook-llm-triage")

webhook_secret_header = APIKeyHeader(name="X-Webhook-Secret", auto_error=False)


def is_valid_webhook_secret(given: str | None) -> bool:
    expected = os.environ.get("WEBHOOK_SECRET", "")
    # Fail closed when no secret is configured; compare bytes so non-ASCII input can't raise.
    return bool(expected) and given is not None and secrets.compare_digest(given.encode(), expected.encode())


def require_webhook_secret(request: Request, given: Annotated[str | None, Depends(webhook_secret_header)]) -> None:
    if not is_valid_webhook_secret(given):
        request.state.error = "unauthorized"
        raise HTTPException(status_code=401, detail="unauthorized")


@app.middleware("http")
async def log_one_line_per_request(request: Request, call_next):
    request.state.event_id = uuid.uuid4().hex
    start = time.monotonic()
    status = 500
    try:
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
    triage = classify(event)
    request.state.outcome = "processed"
    return IntakeResponse(event_id=request.state.event_id, status="processed", triage=triage)
