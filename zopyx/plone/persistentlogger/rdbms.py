"""Small SQLite reference adapter for the RDBMS repository boundary.

Deployments may replace this adapter with SQLAlchemy/PostgreSQL without
changing the application services.  SQLite is useful for local development,
contract tests, and single-process installations.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from .errors import ConfigurationError
from .models import LogEvent
from .repository import MemoryRepository


class SQLiteRepository(MemoryRepository):
    backend = "rdbms"

    def __init__(self, object_uid_value: str, database: str = ":memory:", *, transaction_mode: str = "outbox"):
        if transaction_mode not in {"joined", "outbox", "independent"}:
            raise ConfigurationError("invalid RDBMS transaction mode")
        self.transaction_mode = transaction_mode
        self.connection = sqlite3.connect(database)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()
        super().__init__(object_uid_value)
        self._load()

    def _create_schema(self) -> None:
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS audit_schema_version (
                version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_event (
                object_uid TEXT NOT NULL,
                event_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                occurred_at TEXT,
                actor TEXT NOT NULL,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                target TEXT,
                comment TEXT NOT NULL,
                info_url TEXT,
                details TEXT,
                schema_version INTEGER NOT NULL,
                sequence INTEGER NOT NULL,
                previous_digest TEXT NOT NULL,
                integrity_digest TEXT NOT NULL,
                PRIMARY KEY (object_uid, event_id)
            );
            CREATE INDEX IF NOT EXISTS audit_event_object_time
              ON audit_event (object_uid, created_at DESC, sequence DESC, event_id);
            CREATE INDEX IF NOT EXISTS audit_event_actor ON audit_event (object_uid, actor);
            CREATE INDEX IF NOT EXISTS audit_event_type ON audit_event (object_uid, event_type);
            CREATE INDEX IF NOT EXISTS audit_event_severity ON audit_event (object_uid, severity);
            CREATE TABLE IF NOT EXISTS audit_governance (
                object_uid TEXT NOT NULL, created_at TEXT NOT NULL,
                event_type TEXT NOT NULL, actor TEXT NOT NULL, details TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_retention_policy (
                object_uid TEXT PRIMARY KEY, enabled INTEGER NOT NULL,
                older_than_days INTEGER NOT NULL, max_entries INTEGER NOT NULL,
                policy_id TEXT, legal_basis TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_legal_hold (
                object_uid TEXT NOT NULL, hold_id TEXT NOT NULL,
                event_id TEXT, reason TEXT NOT NULL, actor TEXT NOT NULL,
                created_at TEXT NOT NULL, released_at TEXT, released_by TEXT,
                PRIMARY KEY (object_uid, hold_id)
            );
            CREATE TABLE IF NOT EXISTS audit_deletion_preview (
                object_uid TEXT NOT NULL, preview_id TEXT NOT NULL,
                event_ids TEXT NOT NULL, selection_digest TEXT NOT NULL,
                actor TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                PRIMARY KEY (object_uid, preview_id)
            );
            CREATE TABLE IF NOT EXISTS audit_chain_head (
                object_uid TEXT PRIMARY KEY, sequence INTEGER NOT NULL,
                integrity_digest TEXT NOT NULL
            );
        """)
        if self.connection.execute("SELECT COUNT(*) AS count FROM audit_schema_version").fetchone()["count"] == 0:
            self.connection.execute("INSERT INTO audit_schema_version(version) VALUES (1)")
        self.connection.commit()

    def _load(self) -> None:
        rows = self.connection.execute("SELECT * FROM audit_event WHERE object_uid = ? ORDER BY sequence", (self.object_uid,)).fetchall()
        self._events = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item["details"]) if item["details"] is not None else None
            self._events.append(item)

    def _persist_events(self) -> None:
        self.connection.execute("DELETE FROM audit_event WHERE object_uid = ?", (self.object_uid,))
        for row in self._events:
            self.connection.execute("""INSERT INTO audit_event
                (object_uid,event_id,created_at,occurred_at,actor,event_type,severity,target,comment,info_url,details,schema_version,sequence,previous_digest,integrity_digest)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    self.object_uid, row["event_id"], row["created_at"], row["occurred_at"], row["actor"], row["event_type"], row["severity"], row["target"], row["comment"], row["info_url"], json.dumps(row["details"], ensure_ascii=False, sort_keys=True), row["schema_version"], row["sequence"], row["previous_digest"], row["integrity_digest"],
                ))
        self.connection.commit()

    def append(self, event: LogEvent) -> dict[str, Any]:
        result = super().append(event)
        self._persist_events()
        return result

    def delete_preview(self, preview_id: UUID | str, *, actor: str, reason: str) -> dict[str, Any]:
        result = super().delete_preview(preview_id, actor=actor, reason=reason)
        self._persist_events()
        return result

    def health(self) -> dict[str, Any]:
        result = super().health()
        result.update(schema_version=self.connection.execute("SELECT MAX(version) AS max_version FROM audit_schema_version").fetchone()["max_version"], transaction_mode=self.transaction_mode)
        return result

    def close(self) -> None:
        self.connection.close()


class PostgresRepository(MemoryRepository):  # pragma: no cover - exercised by the Docker integration suite
    """PostgreSQL adapter using psycopg 3 and the shared repository contract."""

    backend = "rdbms"

    def __init__(self, object_uid_value: str, database: str, *, transaction_mode: str = "joined"):
        if transaction_mode not in {"joined", "outbox", "independent"}:
            raise ConfigurationError("invalid RDBMS transaction mode")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ConfigurationError("PostgreSQL support requires psycopg") from exc
        self.transaction_mode = transaction_mode
        database = database.replace("postgresql+psycopg2://", "postgresql://").replace("postgresql+psycopg://", "postgresql://")
        self.connection = psycopg.connect(database, row_factory=dict_row)
        self._create_schema()
        super().__init__(object_uid_value)
        self._load()

    def _create_schema(self) -> None:
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS audit_schema_version (version INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS audit_event (
                object_uid TEXT NOT NULL, event_id TEXT NOT NULL, created_at TEXT NOT NULL,
                occurred_at TEXT, actor TEXT NOT NULL, event_type TEXT NOT NULL,
                severity TEXT NOT NULL, target TEXT, comment TEXT NOT NULL, info_url TEXT,
                details JSONB, schema_version INTEGER NOT NULL, sequence INTEGER NOT NULL,
                previous_digest TEXT NOT NULL, integrity_digest TEXT NOT NULL,
                PRIMARY KEY (object_uid, event_id)
            );
            CREATE INDEX IF NOT EXISTS audit_event_object_time
                ON audit_event (object_uid, created_at DESC, sequence DESC, event_id);
        """)
        if self.connection.execute("SELECT COUNT(*) AS count FROM audit_schema_version").fetchone()["count"] == 0:
            self.connection.execute("INSERT INTO audit_schema_version(version) VALUES (1)")
        self.connection.commit()

    def _load(self) -> None:
        rows = self.connection.execute(
            "SELECT * FROM audit_event WHERE object_uid = %s ORDER BY sequence",
            (self.object_uid,),
        ).fetchall()
        self._events = []
        for row in rows:
            item = dict(row)
            self._events.append(item)

    def _persist_events(self) -> None:
        self.connection.execute("DELETE FROM audit_event WHERE object_uid = %s", (self.object_uid,))
        for row in self._events:
            self.connection.execute(
                """INSERT INTO audit_event
                (object_uid,event_id,created_at,occurred_at,actor,event_type,severity,target,
                 comment,info_url,details,schema_version,sequence,previous_digest,integrity_digest)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (self.object_uid, row["event_id"], row["created_at"], row["occurred_at"],
                 row["actor"], row["event_type"], row["severity"], row["target"],
                 row["comment"], row["info_url"], json.dumps(row["details"], ensure_ascii=False),
                 row["schema_version"], row["sequence"], row["previous_digest"],
                 row["integrity_digest"]),
            )
        self.connection.commit()

    def append(self, event: LogEvent) -> dict[str, Any]:
        result = super().append(event)
        self._persist_events()
        return result

    def delete_preview(self, preview_id: UUID | str, *, actor: str, reason: str) -> dict[str, Any]:
        result = super().delete_preview(preview_id, actor=actor, reason=reason)
        self._persist_events()
        return result

    def health(self) -> dict[str, Any]:
        result = super().health()
        result.update(schema_version=self.connection.execute("SELECT MAX(version) AS max_version FROM audit_schema_version").fetchone()["max_version"], transaction_mode=self.transaction_mode)
        return result

    def close(self) -> None:
        self.connection.close()


class DuckDBRepository(MemoryRepository):
    """DuckDB adapter for embedded analytics and contract testing."""

    backend = "rdbms"

    def __init__(self, object_uid_value: str, database: str = ":memory:", *, transaction_mode: str = "independent"):
        if transaction_mode not in {"joined", "outbox", "independent"}:
            raise ConfigurationError("invalid RDBMS transaction mode")
        try:
            import duckdb
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ConfigurationError("DuckDB support requires duckdb") from exc
        self.transaction_mode = transaction_mode
        self.connection = duckdb.connect(database)
        self._create_schema()
        super().__init__(object_uid_value)
        self._load()

    def _create_schema(self) -> None:
        self.connection.execute("""
            CREATE TABLE IF NOT EXISTS audit_event (
                object_uid VARCHAR NOT NULL, event_id VARCHAR NOT NULL, created_at VARCHAR NOT NULL,
                occurred_at VARCHAR, actor VARCHAR NOT NULL, event_type VARCHAR NOT NULL,
                severity VARCHAR NOT NULL, target VARCHAR, comment VARCHAR NOT NULL, info_url VARCHAR,
                details VARCHAR, schema_version INTEGER NOT NULL, sequence INTEGER NOT NULL,
                previous_digest VARCHAR NOT NULL, integrity_digest VARCHAR NOT NULL,
                PRIMARY KEY (object_uid, event_id)
            )
        """)

    def _load(self) -> None:
        cursor = self.connection.execute(
            "SELECT * FROM audit_event WHERE object_uid = ? ORDER BY sequence", (self.object_uid,)
        )
        columns = [item[0] for item in cursor.description]
        self._events = []
        for values in cursor.fetchall():
            item = dict(zip(columns, values))
            item["details"] = json.loads(item["details"]) if item["details"] is not None else None
            self._events.append(item)

    def _persist_events(self) -> None:
        self.connection.execute("DELETE FROM audit_event WHERE object_uid = ?", (self.object_uid,))
        for row in self._events:
            self.connection.execute(
                """INSERT INTO audit_event VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (self.object_uid, row["event_id"], row["created_at"], row["occurred_at"], row["actor"],
                 row["event_type"], row["severity"], row["target"], row["comment"], row["info_url"],
                 json.dumps(row["details"], ensure_ascii=False), row["schema_version"], row["sequence"],
                 row["previous_digest"], row["integrity_digest"]),
            )

    def append(self, event: LogEvent) -> dict[str, Any]:
        result = super().append(event)
        self._persist_events()
        return result

    def close(self) -> None:
        self.connection.close()
