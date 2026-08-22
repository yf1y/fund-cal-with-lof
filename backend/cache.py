from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_FILE = PROJECT_ROOT / ".cache" / "lof_lens.sqlite3"


class LocalCache:
    """Small persistent cache backed by Python's built-in SQLite driver."""

    def __init__(self, path: Path | str = DEFAULT_CACHE_FILE) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_items (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def get(self, key: str) -> dict[str, Any] | list[Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT payload, expires_at FROM cache_items WHERE cache_key = ?",
                (key,),
            ).fetchone()
            if row is None:
                return None
            if float(row[1]) <= time.time():
                connection.execute("DELETE FROM cache_items WHERE cache_key = ?", (key,))
                return None
        try:
            return json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return None

    def set(self, key: str, payload: dict[str, Any] | list[Any], ttl_seconds: int) -> None:
        updated_at = datetime.now().isoformat(timespec="seconds")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO cache_items (cache_key, payload, expires_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    payload = excluded.payload,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (
                    key,
                    json.dumps(payload, ensure_ascii=False),
                    time.time() + ttl_seconds,
                    updated_at,
                ),
            )

    def health(self) -> dict[str, Any]:
        with self._connection() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM cache_items WHERE expires_at > ?", (time.time(),)
            ).fetchone()[0]
        return {
            "backend": "sqlite",
            "persistent": True,
            "path": str(self.path),
            "active_items": count,
            "message": "本机缓存已启用；无需安装数据库服务",
        }
