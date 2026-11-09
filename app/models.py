from typing import Literal

from pydantic import BaseModel, Field


class IntakeEvent(BaseModel):
    """The Google Form fields a Zap posts to /intake."""

    name: str = Field(max_length=200)
    company: str = Field(max_length=200)
    email: str = Field(max_length=320)
    subject: str = Field(max_length=300)
    message: str = Field(min_length=1, max_length=20_000)


class TriageResult(BaseModel):
    category: Literal["quote_request", "support_ticket", "access_request", "renewal_or_billing", "vendor_or_partner", "other"]
    urgency: Literal["low", "medium", "high"]
    summary: str = Field(max_length=280)


class IntakeResponse(BaseModel):
    event_id: str
    status: Literal["processed", "duplicate"]
    triage: TriageResult
