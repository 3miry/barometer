"""Private reports; only approved structured fields may enter detection."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import hmac
import ipaddress
import json
import math
import secrets
import sqlite3
import time

from .catalog import MODEL_CATALOG, infer_variant
from .detect import Complaint, TAXONOMY


MAX_DESCRIPTION = 600
MAX_MODEL_NAME = 80
ALLOWED_CATEGORIES = frozenset((*TAXONOMY.keys(), "other"))
ALLOWED_SURFACES = frozenset(("web", "mobile", "desktop", "api", "unknown"))
ALLOWED_TIMINGS = frozenset(("now", "today", "this-week", "unsure"))
ALLOWED_STATUSES = frozenset(("pending", "approved", "rejected"))
RATE_LIMITS = ((3, 15 * 60), (8, 24 * 60 * 60))
ATTEMPT_RETENTION_SECONDS = 24 * 60 * 60

CATEGORY_OBSERVATION_TEXT = {
    "sluggish": "slow response",
    "lazy": "lazy incomplete response",
    "quality": "quality dropped",
    "refusals": "unexpected refusal",
    "length": "shorter response",
    "other": "other change",
}


class SubmissionError(ValueError):
    pass


class DuplicateSubmission(SubmissionError):
    pass


@dataclass(frozen=True)
class UserReport:
    id: str
    created_at: float
    family: str
    model_name: str | None
    variant: str | None
    category: str
    surface: str
    timing: str
    description: str | None
    status: str = "pending"


def _clean_text(value, field: str, maximum: int, required: bool = False) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise SubmissionError(f"{field} must be text")
    cleaned = " ".join(value.replace("\x00", " ").split())
    if required and not cleaned:
        raise SubmissionError(f"{field} is required")
    if len(cleaned) > maximum:
        raise SubmissionError(f"{field} must be {maximum} characters or fewer")
    return cleaned


def validate_submission(payload: dict, now: float | None = None) -> UserReport:
    if not isinstance(payload, dict):
        raise SubmissionError("submission must be a JSON object")
    if payload.get("website"):
        raise SubmissionError("unable to accept this report")
    if payload.get("consent") is not True:
        raise SubmissionError("aggregation consent is required")

    family = _clean_text(payload.get("family"), "family", 32, required=True)
    if family not in MODEL_CATALOG:
        raise SubmissionError("family is not currently tracked")
    model_name = _clean_text(payload.get("model_name"), "model_name", MAX_MODEL_NAME)
    category = _clean_text(payload.get("category"), "category", 24, required=True)
    if category not in ALLOWED_CATEGORIES:
        raise SubmissionError("category is not recognised")
    surface = _clean_text(payload.get("surface"), "surface", 24, required=True)
    if surface not in ALLOWED_SURFACES:
        raise SubmissionError("surface is not recognised")
    timing = _clean_text(payload.get("timing"), "timing", 24, required=True)
    if timing not in ALLOWED_TIMINGS:
        raise SubmissionError("timing is not recognised")
    description = _clean_text(
        payload.get("description"), "description", MAX_DESCRIPTION)

    return UserReport(
        id="br_" + secrets.token_urlsafe(9),
        created_at=now if now is not None else time.time(),
        family=family,
        model_name=model_name or None,
        variant=infer_variant(family, model_name) if model_name else None,
        category=category,
        surface=surface,
        timing=timing,
        description=description or None,
    )


def author_token(secret: bytes | str, address: str) -> str:
    """Derive a non-reversible rate-limit key without storing an IP address."""
    if isinstance(secret, str):
        secret = secret.encode("utf-8")
    if not secret:
        raise SubmissionError("rate-limit secret is required")
    try:
        canonical = ipaddress.ip_address(address).compressed
    except ValueError as exc:
        raise SubmissionError("client address is invalid") from exc
    return hmac.new(
        secret, canonical.encode("ascii"), hashlib.sha256,
    ).hexdigest()[:32]


SUBMISSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS user_reports(
  id TEXT PRIMARY KEY,
  created_at REAL NOT NULL,
  family TEXT NOT NULL,
  model_name TEXT,
  variant TEXT,
  category TEXT NOT NULL,
  surface TEXT NOT NULL,
  timing TEXT NOT NULL,
  description TEXT,
  status TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  reviewed_at REAL,
  review_note TEXT);
CREATE INDEX IF NOT EXISTS ix_ur_status_created
  ON user_reports(status, created_at);
CREATE INDEX IF NOT EXISTS ix_ur_fingerprint_created
  ON user_reports(fingerprint, created_at);
CREATE TABLE IF NOT EXISTS submission_attempts(
  author_token TEXT NOT NULL,
  attempted_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_sa_author_attempted
  ON submission_attempts(author_token, attempted_at);
CREATE INDEX IF NOT EXISTS ix_sa_attempted
  ON submission_attempts(attempted_at);
"""


def _fingerprint(report: UserReport) -> str:
    payload = asdict(report)
    payload.pop("id")
    payload.pop("created_at")
    payload.pop("status")
    basis = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


class SubmissionStore:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SUBMISSION_SCHEMA)

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    def __enter__(self) -> "SubmissionStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def add(self, report: UserReport, duplicate_window: int = 3600) -> str:
        fingerprint = _fingerprint(report)
        duplicate = self.db.execute(
            "SELECT id FROM user_reports WHERE fingerprint=? AND created_at>=?",
            (fingerprint, report.created_at - duplicate_window),
        ).fetchone()
        if duplicate:
            raise DuplicateSubmission("an identical report was recently received")
        self.db.execute(
            "INSERT INTO user_reports"
            "(id,created_at,family,model_name,variant,category,surface,timing,"
            "description,status,fingerprint) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                report.id, report.created_at, report.family, report.model_name,
                report.variant, report.category, report.surface, report.timing,
                report.description, report.status, fingerprint,
            ),
        )
        self.db.commit()
        return report.id

    def list(self, status: str = "pending", limit: int = 50) -> list[dict]:
        if status not in ALLOWED_STATUSES:
            raise SubmissionError("status is not recognised")
        rows = self.db.execute(
            "SELECT id,created_at,family,model_name,variant,category,surface,"
            "timing,status FROM user_reports WHERE status=? "
            "ORDER BY created_at LIMIT ?",
            (status, max(1, min(limit, 200))),
        ).fetchall()
        return [dict(row) for row in rows]

    def get(self, report_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT id,created_at,family,model_name,variant,category,surface,"
            "timing,description,status,reviewed_at,review_note "
            "FROM user_reports WHERE id=?",
            (report_id,),
        ).fetchone()
        return dict(row) if row else None

    def moderate(self, report_id: str, status: str, note: str = "") -> bool:
        if status not in {"approved", "rejected"}:
            raise SubmissionError("moderation status must be approved or rejected")
        note = _clean_text(note, "review_note", 240)
        cursor = self.db.execute(
            "UPDATE user_reports SET status=?,reviewed_at=?,review_note=? "
            "WHERE id=? AND status='pending'",
            (status, time.time(), note or None, report_id),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def approved_complaints(self, since: float = 0.0) -> list[Complaint]:
        """Return only approved structured observations, never free text."""
        rows = self.db.execute(
            "SELECT id,created_at,family,variant,category FROM user_reports "
            "WHERE status='approved' AND created_at>=? ORDER BY created_at",
            (since,),
        ).fetchall()
        return [
            Complaint(
                ts=row["created_at"],
                source="user",
                model=row["family"],
                text=(
                    f"user observation {row['id']} "
                    f"{CATEGORY_OBSERVATION_TEXT[row['category']]} {row['id']}"
                ),
                variant=row["variant"],
            )
            for row in rows
        ]

    def consume_attempt(
            self, token: str, now: float | None = None,
            limits: tuple[tuple[int, int], ...] = RATE_LIMITS,
            retention_seconds: int = ATTEMPT_RETENTION_SECONDS,
    ) -> tuple[bool, int]:
        """Atomically record an attempt or return its minimum retry delay."""
        if not token:
            raise SubmissionError("author token is required")
        if not limits or any(maximum < 1 or window < 1
                             for maximum, window in limits):
            raise ValueError("rate limits must be positive")
        now = now if now is not None else time.time()
        try:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute(
                "DELETE FROM submission_attempts WHERE attempted_at<?",
                (now - retention_seconds,),
            )
            retry_after = 0
            for maximum, window in limits:
                count, oldest = self.db.execute(
                    "SELECT COUNT(*),MIN(attempted_at) FROM "
                    "submission_attempts WHERE author_token=? AND attempted_at>=?",
                    (token, now - window),
                ).fetchone()
                if count >= maximum:
                    retry_after = max(
                        retry_after,
                        max(1, math.ceil(float(oldest) + window - now)),
                    )
            if retry_after:
                self.db.commit()
                return False, retry_after
            self.db.execute(
                "INSERT INTO submission_attempts(author_token,attempted_at) "
                "VALUES (?,?)",
                (token, now),
            )
            self.db.commit()
            return True, 0
        except Exception:
            self.db.rollback()
            raise

    def prune_attempts(self, before: float) -> int:
        cursor = self.db.execute(
            "DELETE FROM submission_attempts WHERE attempted_at < ?", (before,))
        self.db.commit()
        return cursor.rowcount

    def prune(self, before: float) -> int:
        cursor = self.db.execute(
            "DELETE FROM user_reports WHERE created_at < ?", (before,))
        self.db.commit()
        return cursor.rowcount
