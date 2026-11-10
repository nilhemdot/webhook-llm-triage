from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field


class IntakeEvent(BaseModel):
    """The Google Form fields a Zap posts to /intake."""

    name: str = Field(max_length=200)
    company: str = Field(max_length=200)
    email: str = Field(max_length=320)
    subject: str = Field(max_length=300)
    message: str = Field(min_length=1, max_length=20_000)


FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")


def sheet_safe(summary: str) -> str:
    """Zap A writes the summary into a Google Sheets cell: a leading apostrophe stops it being read as a formula."""
    # Drops the last character of a 280-char summary so the prefixed text still fits the 280 limit.
    return "'" + summary[:279] if summary.startswith(FORMULA_STARTS) else summary


class TriageResult(BaseModel):
    category: Literal["quote_request", "support_ticket", "access_request", "renewal_or_billing", "vendor_or_partner", "other"]
    urgency: Literal["low", "medium", "high"]
    summary: Annotated[str, Field(max_length=280), AfterValidator(sheet_safe)]


class IntakeResponse(BaseModel):
    event_id: str
    status: Literal["processed", "duplicate"]
    triage: TriageResult
