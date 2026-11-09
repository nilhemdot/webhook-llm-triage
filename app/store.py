"""SQLite store in the data dir: processed intake events and their responses, keyed by idempotency key."""

import sqlite3
import threading
import time
from pathlib import Path


class Store:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        # Opened in the lifespan but used from FastAPI's worker threads; the lock serialises access.
        self._db = sqlite3.connect(data_dir / "triage.sqlite3", check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS events (key TEXT PRIMARY KEY, status TEXT, response_json TEXT, created_at REAL)"
            )

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

    def close(self) -> None:
        with self._lock:
            self._db.close()
