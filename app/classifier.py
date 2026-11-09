from collections.abc import Callable

from app.models import IntakeEvent, TriageResult

Classifier = Callable[[IntakeEvent], TriageResult]


def stub_classifier(event: IntakeEvent) -> TriageResult:
    # shortcut: fixed answer until the Claude classifier lands in T2 (#3), which replaces get_classifier.
    return TriageResult(category="other", urgency="low", summary="Stub triage result: classifier not connected yet.")


def get_classifier() -> Classifier:
    """FastAPI dependency: the classifier /intake uses."""
    return stub_classifier
