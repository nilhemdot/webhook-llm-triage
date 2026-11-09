from collections.abc import Callable
from functools import cache
from time import monotonic  # noqa: F401  (tests swap this clock)

import anthropic

from app.models import IntakeEvent, TriageResult

Classifier = Callable[[IntakeEvent], TriageResult]


@cache
def make_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(timeout=8.0, max_retries=1)


def get_anthropic_client() -> anthropic.Anthropic:
    """FastAPI dependency: the Claude client the classifier uses (tests override it)."""
    return make_client()


def stub_classifier(event: IntakeEvent) -> TriageResult:
    # shortcut: fixed answer until the Claude classifier lands in T2 (#3), which replaces get_classifier.
    return TriageResult(category="other", urgency="low", summary="Stub triage result: classifier not connected yet.")


def get_classifier() -> Classifier:
    """FastAPI dependency: the classifier /intake uses."""
    return stub_classifier
