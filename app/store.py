"""SQLite store in the data dir: processed intake events and their responses, keyed by idempotency key and kept 14 days,
and the daily cap's count of LLM-calling intake events per UTC day.
Also the dead-letter file, dead_letter.jsonl, next to it."""

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

RETENTION_S = 14 * 24 * 3600  # processed intake events are kept 14 days, like dead letters (D8)
PURGE_EVERY_S = 3600


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self._dead_letter_path = data_dir / "dead_letter.jsonl"
        # Opened in the lifespan but used from FastAPI's worker threads; the lock serialises access.
        self._db = sqlite3.connect(data_dir / "triage.sqlite3", check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS events (key TEXT PRIMARY KEY, status TEXT, response_json TEXT, created_at REAL)"
            )
            self._db.execute("CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, count INT)")
            self._purged_at = 0.0
            self._purge_expired()

    def _purge_expired(self) -> None:
        """Delete processed intake events older than 14 days; runs at most once an hour. Call with the lock held."""
        now = time.time()
        if now - self._purged_at >= PURGE_EVERY_S:
            self._db.execute("DELETE FROM events WHERE created_at < ?", (now - RETENTION_S,))
            self._purged_at = now

    def take_daily_cap_slot(self, day: str, cap: int) -> bool:
        """Count one LLM-calling intake event for `day`, unless the daily cap is already reached."""
        with self._lock:
            row = self._db.execute("SELECT count FROM usage WHERE day = ?", (day,)).fetchone()
            if (row[0] if row else 0) >= cap:
                return False
            self._db.execute(
                "INSERT INTO usage VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET count = count + 1", (day,)
            )
            return True

    def processed_response(self, key: str) -> str | None:
        with self._lock:
            row = self._db.execute("SELECT response_json FROM events WHERE key = ?", (key,)).fetchone()
        return row and row[0]

    def save_processed(self, key: str, response_json: str) -> None:
        # shortcut: two concurrent deliveries of one key both call Claude and the first save wins; fine for one Zap.
        with self._lock:
            self._purge_expired()  # first, so an expired row with this key can't make the insert below a no-op
            self._db.execute(
                "INSERT OR IGNORE INTO events VALUES (?, 'processed', ?, ?)", (key, response_json, time.time())
            )

    def add_dead_letter(self, dead_letter: dict) -> None:
        # Reopened on every append: the replay command moves the file aside, and a held handle would follow it.
        # 0600 whatever the umask (fchmod also fixes a file made before): it holds full payloads.
        with self._lock:
            fd = os.open(self._dead_letter_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as f:
                os.fchmod(fd, 0o600)
                f.write(json.dumps(dead_letter, ensure_ascii=False) + "\n")

    def close(self) -> None:
        with self._lock:
            self._db.close()
