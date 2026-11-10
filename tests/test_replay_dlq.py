import json
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from fastapi.testclient import TestClient

from conftest import WEBHOOK_SECRET, claude_reply
from scripts.replay_dlq import main as replay

AUTH = {"X-Webhook-Secret": WEBHOOK_SECRET}
REFUSAL = httpx2.Response(200, json=claude_reply(stop_reason="refusal"))
RATE_LIMITED = httpx2.Response(429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}})
ARGS = ["--url", "http://testserver"]  # the secret comes from WEBHOOK_SECRET, set by configured_app


def read(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def dead_letter_one_event(client, fake_claude, intake_event):
    fake_claude.queue(RATE_LIMITED)
    assert client.post("/intake", json=intake_event, headers=AUTH).status_code == 503


def old_entry(intake_event, days=15):
    received_at = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    return {"event_id": "old", "key": "old-key", "received_at": received_at, "reason": "llm_timeout",
            "error": "x", "payload": intake_event}


def test_dry_run_lists_dead_letters_without_payload_and_changes_nothing(
    client, fake_claude, intake_event, data_dir, capsys
):
    dead_letter_one_event(client, fake_claude, intake_event)
    queue = data_dir / "dead_letter.jsonl"
    before = queue.read_bytes()
    capsys.readouterr()
    assert replay([*ARGS, "--dry-run"], client=client) == 0
    out = capsys.readouterr().out
    [letter] = read(queue)
    assert letter["event_id"] in out and "llm_unavailable" in out
    assert intake_event["email"] not in out and intake_event["message"] not in out
    assert queue.read_bytes() == before
    assert fake_claude.calls == 1
    assert not (data_dir / "dead_letter.replayed.jsonl").exists()


def test_replay_resends_dead_letter_to_intake_and_moves_it_to_replayed(client, fake_claude, intake_event, data_dir):
    dead_letter_one_event(client, fake_claude, intake_event)
    [letter] = read(data_dir / "dead_letter.jsonl")
    assert replay(ARGS, client=client) == 0
    assert read(data_dir / "dead_letter.jsonl") == []
    [replayed] = read(data_dir / "dead_letter.replayed.jsonl")
    assert replayed["event_id"] == letter["event_id"]
    assert fake_claude.calls == 2
    # the replay was stored under the dead letter's idempotency key, so a later Zap replay is a duplicate
    assert client.post("/intake", json=intake_event, headers=AUTH).json()["status"] == "duplicate"


def test_replay_that_fails_again_leaves_exactly_one_dead_letter_with_the_new_reason_and_original_received_at(
    client, fake_claude, intake_event, data_dir
):
    dead_letter_one_event(client, fake_claude, intake_event)
    [letter] = read(data_dir / "dead_letter.jsonl")
    fake_claude.queue(REFUSAL)
    assert replay(ARGS, client=client) == 1
    [requeued] = read(data_dir / "dead_letter.jsonl")
    assert requeued["reason"] == "llm_refused" and requeued["error"] != letter["error"]
    assert requeued["key"] == letter["key"]
    assert requeued["received_at"] == letter["received_at"]  # so the 14-day purge still applies
    assert requeued["payload"] == letter["payload"]
    assert read(data_dir / "dead_letter.replayed.jsonl") == []


def test_replay_answered_by_something_other_than_the_app_keeps_the_dead_letter_once(
    client, fake_claude, intake_event, data_dir
):
    dead_letter_one_event(client, fake_claude, intake_event)
    [letter] = read(data_dir / "dead_letter.jsonl")
    proxy_error = httpx2.Client(transport=httpx2.MockTransport(lambda request: httpx2.Response(502, text="Bad Gateway")))
    assert replay(ARGS, client=proxy_error) == 1
    assert read(data_dir / "dead_letter.jsonl") == [letter]


def test_replay_when_service_unreachable_keeps_dead_letter(client, fake_claude, intake_event, data_dir):
    dead_letter_one_event(client, fake_claude, intake_event)
    [letter] = read(data_dir / "dead_letter.jsonl")

    def unreachable(request):
        raise httpx2.ConnectError("down")

    replay(ARGS, client=httpx2.Client(transport=httpx2.MockTransport(unreachable)))
    assert read(data_dir / "dead_letter.jsonl") == [letter]


def test_replay_purges_dead_letters_and_replayed_entries_older_than_14_days(
    client, fake_claude, intake_event, data_dir
):
    data_dir.mkdir(parents=True, exist_ok=True)
    fresh = {**old_entry(intake_event, days=13), "event_id": "fresh-replayed"}
    (data_dir / "dead_letter.jsonl").write_text(json.dumps(old_entry(intake_event)) + "\n")
    (data_dir / "dead_letter.replayed.jsonl").write_text(
        json.dumps({**old_entry(intake_event), "event_id": "old-replayed"}) + "\n" + json.dumps(fresh) + "\n"
    )
    assert replay(ARGS, client=client) == 0
    assert read(data_dir / "dead_letter.jsonl") == []
    assert read(data_dir / "dead_letter.replayed.jsonl") == [fresh]
    assert fake_claude.calls == 0  # purged dead letters are not re-sent


def test_dead_letter_written_by_the_app_during_a_replay_is_kept(configured_app, fake_claude, intake_event, data_dir):
    with TestClient(configured_app) as client:
        dead_letter_one_event(client, fake_claude, intake_event)
        other = {**intake_event, "message": "Another event that fails while the replay runs."}

        def first_replay_succeeds_then_a_new_event_fails(request):
            fake_claude.queue(RATE_LIMITED)
            client.post("/intake", json=other, headers=AUTH)  # new Zap traffic mid-replay
            return httpx2.Response(200, json={"status": "processed"})

        replay(ARGS, client=httpx2.Client(transport=httpx2.MockTransport(first_replay_succeeds_then_a_new_event_fails)))
    [letter] = read(data_dir / "dead_letter.jsonl")
    assert letter["payload"] == other


def test_files_the_replay_creates_are_private_to_the_owner_even_under_a_permissive_umask(
    client, fake_claude, intake_event, data_dir, permissive_umask
):
    dead_letter_one_event(client, fake_claude, intake_event)
    (data_dir / "dead_letter.jsonl").chmod(0o644)  # e.g. written before dead letters were made private
    modes = []

    def unavailable(request):
        modes.append((data_dir / "dead_letter.replaying.jsonl").stat().st_mode & 0o777)
        return httpx2.Response(503, text="proxy error")

    replay(ARGS, client=httpx2.Client(transport=httpx2.MockTransport(unavailable)))
    assert modes == [0o600]
    assert (data_dir / "dead_letter.jsonl").stat().st_mode & 0o777 == 0o600  # the failure was re-queued here
    replay(ARGS, client=httpx2.Client(transport=httpx2.MockTransport(lambda request: httpx2.Response(200))))
    assert (data_dir / "dead_letter.replayed.jsonl").stat().st_mode & 0o777 == 0o600


def test_replay_has_no_secret_argument(client, capsys):
    with pytest.raises(SystemExit):
        replay([*ARGS, "--secret", WEBHOOK_SECRET], client=client)


def test_replay_reads_the_secret_from_the_environment(client, fake_claude, intake_event, data_dir):
    dead_letter_one_event(client, fake_claude, intake_event)
    sent = []

    def record(request):
        sent.append(request.headers["X-Webhook-Secret"])
        return httpx2.Response(200)

    assert replay(ARGS, client=httpx2.Client(transport=httpx2.MockTransport(record))) == 0
    assert sent == [WEBHOOK_SECRET]


@pytest.mark.parametrize("value", [None, ""])
def test_replay_without_webhook_secret_exits_non_zero_and_changes_nothing(
    client, fake_claude, intake_event, data_dir, monkeypatch, capsys, value
):
    dead_letter_one_event(client, fake_claude, intake_event)
    before = (data_dir / "dead_letter.jsonl").read_bytes()
    if value is None:
        monkeypatch.delenv("WEBHOOK_SECRET")
    else:
        monkeypatch.setenv("WEBHOOK_SECRET", value)
    assert replay(ARGS, client=client) != 0
    assert "WEBHOOK_SECRET" in capsys.readouterr().err
    assert (data_dir / "dead_letter.jsonl").read_bytes() == before
    assert fake_claude.calls == 1


def test_replay_warns_when_the_secret_would_be_sent_over_plain_http(client, capsys):
    replay(["--url", "http://localhost:8000"], client=client)
    assert "unencrypted" in capsys.readouterr().err
    replay(["--url", "https://app.example"], client=client)
    assert "unencrypted" not in capsys.readouterr().err
