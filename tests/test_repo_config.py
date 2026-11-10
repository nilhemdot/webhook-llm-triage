from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_env_example_lists_every_setting():
    env_example = (ROOT / ".env.example").read_text()
    for setting in ("WEBHOOK_SECRET=", "# ANTHROPIC_API_KEY=", "ANTHROPIC_MODEL=claude-sonnet-5-5", "DATA_DIR=", "MAX_EVENTS_PER_DAY="):
        assert setting in env_example


def test_ci_workflow_runs_locked_test_suite():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    for step in ("actions/checkout@v7", "astral-sh/setup-uv@v10.3.3", "uv sync --locked --dev", "uv run pytest -q"):
        assert step in ci


def test_readme_shows_ci_badge():
    readme = (ROOT / "README.md").read_text()
    assert "actions/workflows/ci.yml/badge.svg" in readme
