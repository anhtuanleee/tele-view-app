import sqlite3
import time
from pathlib import Path

# cached Telegram file_ids stay valid for the same bot
TTL_SECONDS = 30 * 24 * 3600


class Cache:
    """Maps a source URL to what was already sent (a video's Telegram file_id)."""

    def __init__(self, path: Path):
        self._db = sqlite3.connect(path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS cache (url TEXT PRIMARY KEY, kind TEXT, value TEXT, meta TEXT, created INTEGER)"
        )

    def get(self, url: str) -> tuple[str, str, str] | None:
        row = self._db.execute(
            "SELECT kind, value, meta FROM cache WHERE url = ? AND created > ?", (url, int(time.time()) - TTL_SECONDS)
        ).fetchone()
        return row

    def put(self, url: str, kind: str, value: str, meta: str = "") -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO cache VALUES (?, ?, ?, ?, ?)", (url, kind, value, meta, int(time.time()))
        )
        self._db.commit()
