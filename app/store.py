"""SQLite store in the data dir: processed intake events and their responses, keyed by idempotency key,
and the daily cap's count of LLM-calling intake events per UTC day.
Also the dead-letter file, dead_letter.jsonl, next to it."""

import json
import sqlite3
import threading
import time
from pathlib import Path


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
            self._db.execute(
                "INSERT OR IGNORE INTO events VALUES (?, 'processed', ?, ?)", (key, response_json, time.time())
            )

    def add_dead_letter(self, dead_letter: dict) -> None:
        # Reopened on every append: the replay command moves the file aside, and a held handle would follow it.
        with self._lock, self._dead_letter_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(dead_letter, ensure_ascii=False) + "\n")

    def close(self) -> None:
        with self._lock:
            self._db.close()
