"""Private, reversible sampling controls for external source accounts."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
import sqlite3
import time


ALLOWED_SUPPRESSION_REASONS = frozenset((
    "marketing", "repeated_chatter", "automated", "off_topic", "other",
))
AUTHOR_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
HANDLE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
QUERY_PHRASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 '\-]{0,59}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_suppressions(
  source TEXT NOT NULL,
  author_id TEXT NOT NULL,
  handle_snapshot TEXT,
  reason TEXT NOT NULL,
  active INTEGER NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY(source, author_id));
CREATE INDEX IF NOT EXISTS ix_source_suppressions_active
  ON source_suppressions(source, active);
CREATE TABLE IF NOT EXISTS query_exclusions(
  source TEXT NOT NULL,
  phrase TEXT NOT NULL,
  reason TEXT NOT NULL,
  active INTEGER NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY(source, phrase));
CREATE INDEX IF NOT EXISTS ix_query_exclusions_active
  ON query_exclusions(source, active);
"""


class SamplingControlError(ValueError):
    pass


@dataclass(frozen=True)
class SourceSuppression:
    source: str
    author_id: str
    handle_snapshot: str | None
    reason: str
    active: bool
    created_at: float
    updated_at: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class QueryExclusion:
    source: str
    phrase: str
    reason: str
    active: bool
    created_at: float
    updated_at: float

    def as_dict(self) -> dict:
        return asdict(self)


def _clean_source(value: str) -> str:
    cleaned = str(value or "").strip().casefold()
    if cleaned not in {"x"}:
        raise SamplingControlError("source suppression is not supported")
    return cleaned


def _clean_author_id(value: str) -> str:
    cleaned = str(value or "").strip()
    if not AUTHOR_ID.fullmatch(cleaned):
        raise SamplingControlError("author_id is not valid")
    return cleaned


def _clean_handle(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip().removeprefix("@")
    if not cleaned:
        return None
    if not HANDLE.fullmatch(cleaned):
        raise SamplingControlError("author handle is not valid")
    return cleaned


def _clean_reason(value: str) -> str:
    cleaned = str(value or "").strip().casefold()
    if cleaned not in ALLOWED_SUPPRESSION_REASONS:
        raise SamplingControlError("suppression reason is not recognised")
    return cleaned


def _clean_query_phrase(value: str) -> str:
    cleaned = " ".join(str(value or "").split()).casefold()
    if not QUERY_PHRASE.fullmatch(cleaned):
        raise SamplingControlError("query exclusion phrase is not valid")
    return cleaned


def _row(row: sqlite3.Row) -> SourceSuppression:
    return SourceSuppression(
        source=row["source"], author_id=row["author_id"],
        handle_snapshot=row["handle_snapshot"], reason=row["reason"],
        active=bool(row["active"]), created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _query_row(row: sqlite3.Row) -> QueryExclusion:
    return QueryExclusion(
        source=row["source"], phrase=row["phrase"], reason=row["reason"],
        active=bool(row["active"]), created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


class SamplingControlStore:
    """Small private ledger; deactivation preserves why a rule once existed."""

    def __init__(self, path: str | Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    def __enter__(self) -> "SamplingControlStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def set_source_suppression(
        self,
        source: str,
        author_id: str,
        handle_snapshot: str | None,
        reason: str,
        *,
        active: bool,
        now: float | None = None,
    ) -> SourceSuppression:
        source = _clean_source(source)
        author_id = _clean_author_id(author_id)
        handle_snapshot = _clean_handle(handle_snapshot)
        reason = _clean_reason(reason)
        if not isinstance(active, bool):
            raise SamplingControlError("active must be boolean")
        timestamp = float(now if now is not None else time.time())
        self.db.execute(
            "INSERT INTO source_suppressions"
            "(source,author_id,handle_snapshot,reason,active,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(source,author_id) DO UPDATE SET "
            "handle_snapshot=excluded.handle_snapshot,reason=excluded.reason,"
            "active=excluded.active,updated_at=excluded.updated_at",
            (
                source, author_id, handle_snapshot, reason, int(active),
                timestamp, timestamp,
            ),
        )
        self.db.commit()
        return self.get(source, author_id)

    def get(self, source: str, author_id: str) -> SourceSuppression:
        source = _clean_source(source)
        author_id = _clean_author_id(author_id)
        row = self.db.execute(
            "SELECT source,author_id,handle_snapshot,reason,active,"
            "created_at,updated_at FROM source_suppressions "
            "WHERE source=? AND author_id=?",
            (source, author_id),
        ).fetchone()
        if row is None:
            raise SamplingControlError("source suppression does not exist")
        return _row(row)

    def active(self, source: str) -> list[SourceSuppression]:
        source = _clean_source(source)
        rows = self.db.execute(
            "SELECT source,author_id,handle_snapshot,reason,active,"
            "created_at,updated_at FROM source_suppressions "
            "WHERE source=? AND active=1 ORDER BY author_id",
            (source,),
        ).fetchall()
        return [_row(row) for row in rows]

    def all(self) -> list[SourceSuppression]:
        rows = self.db.execute(
            "SELECT source,author_id,handle_snapshot,reason,active,"
            "created_at,updated_at FROM source_suppressions "
            "ORDER BY source,author_id"
        ).fetchall()
        return [_row(row) for row in rows]

    def set_query_exclusion(
        self,
        source: str,
        phrase: str,
        reason: str,
        *,
        active: bool,
        now: float | None = None,
    ) -> QueryExclusion:
        """Add or reverse a content exclusion without hiding its audit trail."""
        source = _clean_source(source)
        phrase = _clean_query_phrase(phrase)
        reason = _clean_reason(reason)
        if not isinstance(active, bool):
            raise SamplingControlError("active must be boolean")
        timestamp = float(now if now is not None else time.time())
        self.db.execute(
            "INSERT INTO query_exclusions"
            "(source,phrase,reason,active,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(source,phrase) DO UPDATE SET "
            "reason=excluded.reason,active=excluded.active,"
            "updated_at=excluded.updated_at",
            (source, phrase, reason, int(active), timestamp, timestamp),
        )
        self.db.commit()
        return self.get_query_exclusion(source, phrase)

    def get_query_exclusion(
            self, source: str, phrase: str) -> QueryExclusion:
        source = _clean_source(source)
        phrase = _clean_query_phrase(phrase)
        row = self.db.execute(
            "SELECT source,phrase,reason,active,created_at,updated_at "
            "FROM query_exclusions WHERE source=? AND phrase=?",
            (source, phrase),
        ).fetchone()
        if row is None:
            raise SamplingControlError("query exclusion does not exist")
        return _query_row(row)

    def active_query_exclusions(self, source: str) -> list[QueryExclusion]:
        source = _clean_source(source)
        rows = self.db.execute(
            "SELECT source,phrase,reason,active,created_at,updated_at "
            "FROM query_exclusions WHERE source=? AND active=1 "
            "ORDER BY phrase",
            (source,),
        ).fetchall()
        return [_query_row(row) for row in rows]
