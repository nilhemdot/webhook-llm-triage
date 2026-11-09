import json
from datetime import UTC, datetime, timedelta

import httpx2
from fastapi.testclient import TestClient

from conftest import WEBHOOK_SECRET
from scripts.replay_dlq import main as replay

AUTH = {"X-Webhook-Secret": WEBHOOK_SECRET}
RATE_LIMITED = httpx2.Response(429, json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}})
ARGS = ["--url", "http://testserver", "--secret", WEBHOOK_SECRET]


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


def test_replay_that_fails_again_leaves_exactly_one_dead_letter_with_original_received_at(
    client, fake_claude, intake_event, data_dir
):
    dead_letter_one_event(client, fake_claude, intake_event)
    [letter] = read(data_dir / "dead_letter.jsonl")
    fake_claude.queue(RATE_LIMITED)
    replay(ARGS, client=client)
    assert read(data_dir / "dead_letter.jsonl") == [letter]
    assert read(data_dir / "dead_letter.replayed.jsonl") == []


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
