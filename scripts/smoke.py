"""One real call to Claude through the classifier, to check structured output and fallbacks on the configured model.

Costs well under a cent. Needs a credential the SDK finds on its own: `ant auth login` on your machine,
or ANTHROPIC_API_KEY. Run from the repo root:

    uv run python scripts/smoke.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # so `app` imports when run as a script

from app.classifier import classify, make_client, model  # noqa: E402
from app.models import IntakeEvent  # noqa: E402

SAMPLE_INTAKE_EVENT = IntakeEvent(
    name="Smoke Test",
    company="Example Corp",
    email="smoke@example.com",
    subject="Cannot log in since this morning",
    message="None of our 12 staff can sign in to the VPN since 8am, so nobody can work. Please help urgently.",
)


def main() -> None:
    triage = classify(make_client(), SAMPLE_INTAKE_EVENT)
    print(f"model={model()} {triage.model_dump_json()}")


if __name__ == "__main__":
    main()
