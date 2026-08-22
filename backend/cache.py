from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE_FILE = PROJECT_ROOT / ".cache" / "fundcal.sqlite3"
LEGACY_CACHE_FILE = PROJECT_ROOT / ".cache" / "lof_lens.sqlite3"


class LocalCache:
    """Query cache and history: PostgreSQL in cloud, SQLite on a local machine."""

    def __init__(
        self,
        path: Path | str = DEFAULT_CACHE_FILE,
        database_url: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.database_url = (database_url if database_url is not None else os.getenv("DATABASE_URL", "")).strip()
        self._engine = None
        self._cache_table = None
        self._history_table = None
        self.backend = "sqlite"
        self.message = "本机 SQLite 查询库已启用；无需安装数据库服务"
        if self.database_url:
            try:
                self._initialize_postgres()
                return
            except Exception as exc:
                self.backend = "sqlite-fallback"
                self.message = f"PostgreSQL 连接失败，已回退本机 SQLite：{type(exc).__name__}"
        if self.path == DEFAULT_CACHE_FILE and not self.path.exists() and LEGACY_CACHE_FILE.exists():
            self.path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(LEGACY_CACHE_FILE, self.path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_sqlite()

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

    def _initialize_sqlite(self) -> None:
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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS search_history (
                    fund_code TEXT PRIMARY KEY,
                    fund_name TEXT NOT NULL,
                    result_payload TEXT NOT NULL,
                    last_queried_at TEXT NOT NULL
                )
                """
            )

    @staticmethod
    def _normalized_database_url(value: str) -> str:
        if value.startswith("postgres://"):
            return "postgresql+psycopg://" + value[len("postgres://") :]
        if value.startswith("postgresql://"):
            return "postgresql+psycopg://" + value[len("postgresql://") :]
        return value

    def _initialize_postgres(self) -> None:
        from sqlalchemy import JSON, Column, Float, MetaData, String, Table, create_engine

        metadata = MetaData()
        self._cache_table = Table(
            "runtime_cache",
            metadata,
            Column("cache_key", String(160), primary_key=True),
            Column("payload", JSON, nullable=False),
            Column("expires_at", Float, nullable=False),
            Column("updated_at", String(32), nullable=False),
        )
        self._history_table = Table(
            "search_history",
            metadata,
            Column("fund_code", String(6), primary_key=True),
            Column("fund_name", String(255), nullable=False),
            Column("result_payload", JSON, nullable=False),
            Column("last_queried_at", String(32), nullable=False),
        )
        self._engine = create_engine(
            self._normalized_database_url(self.database_url), pool_pre_ping=True
        )
        metadata.create_all(self._engine)
        with self._engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        self.backend = "postgresql"
        self.message = "Supabase/PostgreSQL 查询库已启用"

    def get(self, key: str) -> dict[str, Any] | list[Any] | None:
        if self._engine is not None:
            from sqlalchemy import delete, select

            with self._engine.begin() as connection:
                row = connection.execute(
                    select(self._cache_table.c.payload, self._cache_table.c.expires_at).where(
                        self._cache_table.c.cache_key == key
                    )
                ).first()
                if row is None:
                    return None
                if float(row.expires_at) <= time.time():
                    connection.execute(
                        delete(self._cache_table).where(self._cache_table.c.cache_key == key)
                    )
                    return None
                return row.payload
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
        if self._engine is not None:
            from sqlalchemy import delete

            with self._engine.begin() as connection:
                connection.execute(
                    delete(self._cache_table).where(self._cache_table.c.cache_key == key)
                )
                connection.execute(
                    self._cache_table.insert().values(
                        cache_key=key,
                        payload=payload,
                        expires_at=time.time() + ttl_seconds,
                        updated_at=updated_at,
                    )
                )
            return
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

    def record_search(self, result: dict[str, Any]) -> None:
        code = str(result.get("fund_code", "")).zfill(6)
        name = str(result.get("fund_name") or code)
        queried_at = datetime.now().isoformat(timespec="microseconds")
        saved_result = dict(result)
        saved_result["last_queried_at"] = queried_at
        if self._engine is not None:
            from sqlalchemy import delete

            with self._engine.begin() as connection:
                connection.execute(
                    delete(self._history_table).where(self._history_table.c.fund_code == code)
                )
                connection.execute(
                    self._history_table.insert().values(
                        fund_code=code,
                        fund_name=name,
                        result_payload=saved_result,
                        last_queried_at=queried_at,
                    )
                )
            return
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO search_history (fund_code, fund_name, result_payload, last_queried_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(fund_code) DO UPDATE SET
                    fund_name = excluded.fund_name,
                    result_payload = excluded.result_payload,
                    last_queried_at = excluded.last_queried_at
                """,
                (code, name, json.dumps(saved_result, ensure_ascii=False), queried_at),
            )

    def list_search_history(self, limit: int = 50) -> list[dict[str, Any]]:
        if self._engine is not None:
            from sqlalchemy import desc, select

            with self._engine.connect() as connection:
                rows = connection.execute(
                    select(self._history_table.c.result_payload)
                    .order_by(desc(self._history_table.c.last_queried_at))
                    .limit(limit)
                ).all()
            return [dict(row.result_payload) for row in rows]
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT result_payload FROM search_history
                ORDER BY last_queried_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            try:
                result.append(json.loads(row[0]))
            except json.JSONDecodeError:
                continue
        return result

    def health(self) -> dict[str, Any]:
        if self._engine is not None:
            from sqlalchemy import func, select

            with self._engine.connect() as connection:
                count = connection.execute(
                    select(func.count()).select_from(self._cache_table).where(
                        self._cache_table.c.expires_at > time.time()
                    )
                ).scalar_one()
                history_count = connection.execute(
                    select(func.count()).select_from(self._history_table)
                ).scalar_one()
        else:
            with self._connection() as connection:
                count = connection.execute(
                    "SELECT COUNT(*) FROM cache_items WHERE expires_at > ?", (time.time(),)
                ).fetchone()[0]
                history_count = connection.execute(
                    "SELECT COUNT(*) FROM search_history"
                ).fetchone()[0]
        return {
            "backend": self.backend,
            "persistent": True,
            "path": str(self.path) if self._engine is None else None,
            "active_items": count,
            "search_history_count": history_count,
            "message": self.message,
        }
