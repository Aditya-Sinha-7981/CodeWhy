"""SQLite persistence for validated summarization results."""

from __future__ import annotations

import json
import gc
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codewhy.summarize.result import PassResult
from codewhy.summarize.schemas import FilePass, ModulePass, ProjectPass

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "1"
_MODELS = {"file": FilePass, "module": ModulePass, "project": ProjectPass}


class CacheError(Exception):
    """Base class for cache failures that should be surfaced to the CLI."""


class CacheLocked(CacheError):
    """Another process holds the cache database lock."""


class CacheReadOnly(CacheError):
    """The cache directory or database cannot be written."""


class CacheIOError(CacheError):
    """A recoverable cache operation failed during a run."""


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class CacheDB:
    """Lazy SQLite cache with immediate commits and corruption recovery."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection: sqlite3.Connection | None = None

    def _locked(self) -> CacheLocked:
        return CacheLocked(f"Another codewhy run seems to be using {self.path}. Wait for it to finish or delete the lock.")

    @staticmethod
    def _is_lock_error(exc: sqlite3.Error) -> bool:
        code = getattr(exc, "sqlite_errorcode", None)
        lock_codes = {getattr(sqlite3, "SQLITE_BUSY", 5), getattr(sqlite3, "SQLITE_LOCKED", 6)}
        return code in lock_codes or any(word in str(exc).lower() for word in ("locked", "busy"))

    def _error(self, exc: sqlite3.Error) -> CacheError:
        message = str(exc).lower()
        if self._is_lock_error(exc):
            return self._locked()
        if "readonly" in message or "read-only" in message or "permission denied" in message or "unable to open" in message:
            return CacheReadOnly(f"cannot write cache database {self.path}: {exc}")
        if isinstance(exc, (sqlite3.OperationalError, sqlite3.DatabaseError)):
            return CacheIOError(str(exc))
        return CacheIOError(str(exc))

    def _ensure_directory(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            marker = self.path.parent / ".gitignore"
            if not marker.exists():
                marker.write_text("*\n", encoding="utf-8", newline="\n")
        except OSError as exc:
            raise CacheReadOnly(f"cannot write cache directory {self.path.parent}: {exc}") from None

    def _connect(self) -> sqlite3.Connection:
        self._ensure_directory()
        try:
            connection = sqlite3.connect(self.path, timeout=5)
            connection.execute("PRAGMA busy_timeout = 5000")
            return connection
        except sqlite3.Error as exc:
            raise self._error(exc) from None
        except OSError as exc:
            raise CacheReadOnly(f"cannot write cache database {self.path}: {exc}") from None

    def _create_schema(self, connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS pass_cache (
                pass TEXT NOT NULL,
                subject TEXT NOT NULL,
                input_key TEXT NOT NULL,
                result_json TEXT NOT NULL,
                generated_at TIMESTAMP NOT NULL,
                PRIMARY KEY (pass, subject)
            );
            """
        )
        row = connection.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is not None and row[0] != SCHEMA_VERSION:
            raise ValueError("unknown schema version")
        connection.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
        connection.commit()

    def _rebuild(self) -> sqlite3.Connection:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        gc.collect()
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise CacheReadOnly(f"cannot rebuild cache database {self.path}: {exc}") from None
        connection = self._connect()
        try:
            self._create_schema(connection)
        except Exception:
            connection.close()
            raise
        return connection

    def _open(self) -> sqlite3.Connection:
        if self._connection is not None:
            return self._connection
        connection = self._connect()
        try:
            self._create_schema(connection)
        except ValueError:
            connection.close()
            del connection
            logger.warning("cache unreadable, rebuilding")
            self._connection = self._rebuild()
            return self._connection
        except sqlite3.DatabaseError as exc:
            connection.close()
            del connection
            classified = self._error(exc)
            if isinstance(classified, (CacheLocked, CacheReadOnly)):
                raise classified from None
            logger.warning("cache unreadable, rebuilding")
            self._connection = self._rebuild()
            return self._connection
        except sqlite3.Error as exc:
            connection.close()
            del connection
            raise self._error(exc) from None
        self._connection = connection
        return connection

    def ensure_open(self) -> None:
        """Open and validate the database before a pipeline begins."""
        self._open()

    def _run(self, operation: Any) -> Any:
        try:
            return operation(self._open())
        except CacheError:
            raise
        except sqlite3.Error as exc:
            raise self._error(exc) from None

    def lookup(self, pass_name: str, subject: str, input_key: str) -> PassResult | None:
        if self._connection is None and not self.path.exists():
            return None
        row = self._run(
            lambda connection: connection.execute(
                "SELECT input_key, result_json, generated_at FROM pass_cache WHERE pass = ? AND subject = ?",
                (pass_name, subject),
            ).fetchone()
        )
        if row is None or row[0] != input_key:
            return None
        try:
            payload = json.loads(row[1])
            model_cls = _MODELS[pass_name]
            data = model_cls.model_validate(payload["data"])
            generated_at = _parse_timestamp(payload["generated_at"])
            if payload.get("metadata") != {}:
                return None
            return PassResult.ok(data, generated_at)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def peek(self, pass_name: str, subject: str, input_key: str) -> bool:
        """Read-only key lookup for the future dry-run command."""
        if not self.path.exists():
            return False
        if self._connection is not None:
            connection = self._connection
            row = connection.execute(
                "SELECT input_key FROM pass_cache WHERE pass = ? AND subject = ?",
                (pass_name, subject),
            ).fetchone()
            return row is not None and row[0] == input_key
        uri = self.path.resolve().as_uri() + "?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=5)
            connection.execute("PRAGMA busy_timeout = 5000")
            row = connection.execute(
                "SELECT input_key FROM pass_cache WHERE pass = ? AND subject = ?",
                (pass_name, subject),
            ).fetchone()
            return row is not None and row[0] == input_key
        except sqlite3.Error as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise self._locked() from None
            return False
        finally:
            if "connection" in locals():
                connection.close()

    def store(self, pass_name: str, subject: str, input_key: str, result: PassResult) -> None:
        if result.failure is not None or result.data is None:
            return
        payload = {
            "data": result.data.model_dump(mode="json"),
            "metadata": {},
            "generated_at": _timestamp(result.generated_at),
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

        def write(connection: sqlite3.Connection) -> None:
            connection.execute(
                """INSERT INTO pass_cache(pass, subject, input_key, result_json, generated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(pass, subject) DO UPDATE SET
                     input_key = excluded.input_key,
                     result_json = excluded.result_json,
                     generated_at = excluded.generated_at""",
                (pass_name, subject, input_key, serialized, payload["generated_at"]),
            )
            connection.commit()

        self._run(write)

    def delete_missing(self, pass_name: str, keep_subjects: set[str]) -> int:
        if self._connection is None and not self.path.exists():
            return 0
        def delete(connection: sqlite3.Connection) -> int:
            rows = connection.execute("SELECT subject FROM pass_cache WHERE pass = ?", (pass_name,)).fetchall()
            stale = [row[0] for row in rows if row[0] not in keep_subjects]
            connection.executemany("DELETE FROM pass_cache WHERE pass = ? AND subject = ?", [(pass_name, s) for s in stale])
            connection.commit()
            return len(stale)

        return self._run(delete)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
