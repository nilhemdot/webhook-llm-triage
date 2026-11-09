"""Replay dead letters to /intake, and purge dead letters and replayed entries older than 14 days (D8).

    DATA_DIR=/var/data uv run python scripts/replay_dlq.py --url https://<app>.onrender.com --secret "$WEBHOOK_SECRET" [--dry-run]

Successes (200, including "duplicate") move to dead_letter.replayed.jsonl; failures stay in dead_letter.jsonl
with their original received_at, so the 14-day purge still applies to them. Prints ids and reasons, never payloads.
"""

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2

RETENTION = timedelta(days=14)
REPLAY_HEADER = "X-Dead-Letter-Replay"  # tells the app not to dead-letter the replay again; we keep failures here


def read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line] if path.exists() else []


def lines(entries: list[dict]) -> str:
    return "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries)


def main(argv: list[str] | None = None, client: httpx2.Client | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="service base URL, e.g. https://<app>.onrender.com")
    parser.add_argument("--secret", required=True, help="the webhook secret")
    parser.add_argument("--dry-run", action="store_true", help="list dead letters; send and change nothing")
    args = parser.parse_args(argv)

    data_dir = Path(os.environ.get("DATA_DIR", "./data"))
    queue = data_dir / "dead_letter.jsonl"
    working = data_dir / "dead_letter.replaying.jsonl"  # left over only if an earlier run crashed
    replayed = data_dir / "dead_letter.replayed.jsonl"
    cutoff = datetime.now(UTC) - RETENTION

    def expired(entry: dict) -> bool:
        return datetime.fromisoformat(entry["received_at"]) < cutoff

    if args.dry_run:
        for e in read(working) + read(queue):
            print(e["event_id"], e["received_at"], e["reason"], "(older than 14 days: would be purged)" if expired(e) else "")
        return 0

    if not working.exists() and queue.exists():
        queue.replace(working)  # the app's next dead letter starts a fresh queue file, so nothing it writes is lost
    client = client or httpx2.Client(timeout=30.0)
    done, failed = [], []
    for e in read(working):
        if expired(e):
            print(e["event_id"], "purged")
            continue
        payload = e["payload"]
        try:
            response = client.post(
                f"{args.url.rstrip('/')}/intake",
                content=payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False).encode(),
                headers={"Content-Type": "application/json", "X-Webhook-Secret": args.secret,
                         "Idempotency-Key": e["key"], REPLAY_HEADER: "1"},
            )
            status = response.status_code
        except httpx2.TransportError as exc:
            status = type(exc).__name__
        print(e["event_id"], e["reason"], "->", status)
        if status == 200:
            done.append({**e, "replayed_at": datetime.now(UTC).isoformat()})
        else:
            failed.append(e)

    replayed.parent.mkdir(parents=True, exist_ok=True)
    replayed.write_text(lines([e for e in read(replayed) if not expired(e)] + done), encoding="utf-8")
    with queue.open("a", encoding="utf-8") as f:
        f.write(lines(failed))
    working.unlink(missing_ok=True)
    print(f"replayed {len(done)}, still failing {len(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
