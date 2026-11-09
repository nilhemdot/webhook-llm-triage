"""Replay dead letters to /intake, and purge dead letters and replayed entries older than 14 days (D8).

    DATA_DIR=/var/data uv run python scripts/replay_dlq.py --url https://<app>.onrender.com [--dry-run]

The webhook secret is read from the WEBHOOK_SECRET environment variable (already set in the Render Shell),
never from the command line, where it would land in shell history and the process list.

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


def write_private(path: Path, text: str, append: bool = False) -> None:
    """Write a file only its owner can read (0600 whatever the umask): dead letters hold full payloads."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC), 0o600)
    with os.fdopen(fd, "a" if append else "w", encoding="utf-8") as f:
        os.fchmod(fd, 0o600)
        f.write(text)


def main(argv: list[str] | None = None, client: httpx2.Client | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="service base URL, e.g. https://<app>.onrender.com")
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

    secret = os.environ.get("WEBHOOK_SECRET", "")
    if not secret:
        print("replay_dlq: WEBHOOK_SECRET is not set; set it in the environment, not on the command line", file=sys.stderr)
        return 2
    if args.url.startswith("http://"):
        print("replay_dlq: warning: --url is http://, so the webhook secret is sent unencrypted (fine for localhost only)",
              file=sys.stderr)

    if not working.exists() and queue.exists():
        queue.replace(working)  # the app's next dead letter starts a fresh queue file, so nothing it writes is lost
        working.chmod(0o600)  # a rename keeps the old mode
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
                headers={"Content-Type": "application/json", "X-Webhook-Secret": secret,
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
    write_private(replayed, lines([e for e in read(replayed) if not expired(e)] + done))
    write_private(queue, lines(failed), append=True)
    working.unlink(missing_ok=True)
    print(f"replayed {len(done)}, still failing {len(failed)}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
