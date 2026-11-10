"""The deploy config and docs say what the code does: these checks fail when they drift apart."""

import re
from pathlib import Path

from app.main import LLM_FAILURES

ROOT = Path(__file__).resolve().parent.parent
APP_SOURCE = "\n".join(p.read_text() for p in (ROOT / "app").glob("*.py"))


def failure_rows() -> set[tuple[str, str]]:
    """(status code, error) for every non-2xx response the app returns."""
    rows = {(str(code), error) for _, code, error in LLM_FAILURES}
    rows |= {(code, error) for code, error in re.findall(r'status_code=(\d{3}), content=\{"(?:error|detail)": "(\w+)"', APP_SOURCE)}
    rows.add(("500", "internal_error"))  # an unexpected exception: Starlette's plain-text 500, logged as internal_error
    return rows


def test_render_blueprint_has_paid_plan_disk_health_check_and_prompted_secrets():
    blueprint = (ROOT / "render.yaml").read_text()
    for line in (
        "runtime: python",
        "plan: starter",
        "buildCommand: uv sync --locked --no-dev",
        "startCommand: uv run --no-dev uvicorn app.main:app --host 0.0.0.0 --port $PORT --no-access-log",
        "healthCheckPath: /healthz",
        "mountPath: /var/data",
        "sizeGB: 1",
    ):
        assert line in blueprint
    for secret in ("WEBHOOK_SECRET", "ANTHROPIC_API_KEY"):
        assert re.search(rf"key: {secret}\n\s+sync: false", blueprint), f"{secret} must be prompted, not committed"
    assert re.search(r"key: DATA_DIR\n\s+value: /var/data", blueprint)


def test_readme_failure_mode_table_covers_every_status_code_the_app_returns():
    readme = (ROOT / "README.md").read_text()
    assert len(failure_rows()) >= 11  # guards the regex above against silently matching nothing
    for code, error in failure_rows():
        assert re.search(rf"^\| {code} \| `{error}` \|", readme, re.M), f"no failure-mode row for {code} {error}"


def test_readme_has_diagram_quick_start_and_known_limitations():
    readme = (ROOT / "README.md").read_text()
    for section in ("```mermaid", "## Quick start", "uv sync", "uv run pytest -q", "## Known limitations"):
        assert section in readme


def test_every_relative_link_in_the_docs_resolves():
    for doc in ("README.md", "RUNBOOK.md", "SECURITY.md", "docs/UAT.md", "docs/FAILURE_TRACE.md"):
        path = ROOT / doc
        for target in re.findall(r"\]\((?!https?://|#)([^)#\s]+)", path.read_text()):
            assert (path.parent / target).exists(), f"{doc} links to missing {target}"


def test_runbook_covers_zap_statuses_replay_rotation_and_escalation():
    runbook = (ROOT / "RUNBOOK.md").read_text()
    for topic in ("Zap run statuses", "Zap replay", "scripts/replay_dlq.py", "Rotate the webhook secret", "Rotate the Anthropic API key", "Escalation"):
        assert topic in runbook


def test_security_covers_secrets_logs_retention_cost_and_prompt_injection():
    security = (ROOT / "SECURITY.md").read_text()
    for topic in ("compare_digest", "never logged", "14 days", "MAX_EVENTS_PER_DAY", "spend limit", "Prompt injection", "Synthetic data only", "formula"):
        assert topic in security


def test_uat_and_failure_trace_are_templates_with_placeholders():
    for doc in ("docs/UAT.md", "docs/FAILURE_TRACE.md"):
        assert "[X]" in (ROOT / doc).read_text()


def test_readme_has_the_ci_badge():
    readme = (ROOT / "README.md").read_text()
    workflow = "https://github.com/nilhemdot/webhook-llm-triage/actions/workflows/ci.yml"
    assert f"[![CI]({workflow}/badge.svg)]({workflow})" in readme


def test_readme_and_script_replay_commands_match_the_runbook():
    replay_commands = lambda text: re.findall(r"^\s*(uv run .*replay_dlq\.py.*?)\s*$", text, re.M)  # noqa: E731
    runbook = replay_commands((ROOT / "RUNBOOK.md").read_text())
    assert runbook and all("--no-dev" in c and "--secret" not in c for c in runbook)
    assert replay_commands((ROOT / "README.md").read_text()) == runbook
    usage = replay_commands((ROOT / "scripts" / "replay_dlq.py").read_text())
    assert usage == [runbook[-1] + " [--dry-run]"]
