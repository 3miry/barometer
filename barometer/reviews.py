"""Private human decisions for classifier shadow review.

The review database stores structured decisions and a source fingerprint only;
raw report text remains in the separately retained source database.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
import time

from .catalog import MODEL_CATALOG
from .classifier import CLASSIFIER_VERSION
from .vocabulary import (
    CodedObservation,
    concepts_by_id,
    validate_coded_observations,
)


ALLOWED_REVIEW_STATUSES = frozenset((
    "approved", "corrected", "rejected", "deferred",
))
MAX_REVIEW_NOTE = 600
MAX_NOVELTY_CANDIDATES = 20
MAX_NOVELTY_LABEL = 80


class ReviewError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewDecision:
    unit_id: str
    source_report_id: str
    source_fingerprint: str
    classifier_version: str
    status: str
    target_family: str | None
    target_variant: str | None
    observations: tuple[CodedObservation, ...]
    novelty_candidates: tuple[str, ...]
    review_note: str | None
    reviewed_at: float


REVIEW_SCHEMA = """
CREATE TABLE IF NOT EXISTS classifier_review_units(
  unit_id TEXT PRIMARY KEY,
  source_report_id TEXT NOT NULL,
  source_fingerprint TEXT NOT NULL,
  classifier_version TEXT NOT NULL,
  status TEXT NOT NULL,
  target_family TEXT,
  target_variant TEXT,
  observations_json TEXT NOT NULL,
  novelty_json TEXT NOT NULL,
  review_note TEXT,
  reviewed_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ix_cr_status_reviewed
  ON classifier_review_units(status, reviewed_at);
CREATE INDEX IF NOT EXISTS ix_cr_source
  ON classifier_review_units(source_report_id);
"""


def source_fingerprint(report_id: str, text: str) -> str:
    return hashlib.sha256(
        f"{report_id}\0{text}".encode("utf-8")
    ).hexdigest()[:24]


def review_unit_id(
    source_report_id: str,
    seed_family: str | None,
    seed_variant: str | None,
) -> str:
    """Stable reviewer unit: one source report viewed through one model target."""
    target = seed_variant or seed_family or "unresolved"
    return f"{source_report_id}::{target}"


def _clean_text(value, field: str, maximum: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ReviewError(f"{field} must be text")
    cleaned = " ".join(value.replace("\x00", " ").split())
    if len(cleaned) > maximum:
        raise ReviewError(f"{field} must be {maximum} characters or fewer")
    return cleaned


def _valid_variant_keys(family: str) -> set[str]:
    return {
        item["key"]
        for item in MODEL_CATALOG[family].get("tracked_variants", ())
    }


def validate_review_decision(
    unit_id: str,
    source_report_id: str,
    source_hash: str,
    payload: dict,
    *,
    now: float | None = None,
) -> ReviewDecision:
    if not isinstance(payload, dict):
        raise ReviewError("review must be a JSON object")
    unit_id = _clean_text(unit_id, "unit_id", 240)
    source_report_id = _clean_text(source_report_id, "source_report_id", 128)
    source_hash = _clean_text(source_hash, "source_fingerprint", 64)
    if not unit_id or not source_report_id or not source_hash:
        raise ReviewError(
            "review unit, source report id, and source fingerprint are required")
    status = payload.get("status")
    if status not in ALLOWED_REVIEW_STATUSES:
        raise ReviewError("review status is not recognised")
    family = _clean_text(payload.get("target_family"), "target_family", 32)
    if family and family not in MODEL_CATALOG:
        raise ReviewError("target family is not tracked")
    variant = _clean_text(payload.get("target_variant"), "target_variant", 80)
    if variant:
        if not family:
            raise ReviewError("target variant requires a target family")
        if variant not in _valid_variant_keys(family):
            raise ReviewError("target variant does not belong to target family")

    observations_raw = payload.get("observations", [])
    try:
        observations = validate_coded_observations(observations_raw)
    except ValueError as exc:
        raise ReviewError(str(exc)) from exc
    concepts = concepts_by_id()
    superseded = [
        item.concept_id
        for item in observations
        if concepts[item.concept_id].status == "superseded"
    ]
    if superseded:
        raise ReviewError(
            "superseded observations must be replaced before saving")
    if any(item.claim_status != "reported" for item in observations):
        raise ReviewError("human coding cannot promote causal claim status")
    novelty_raw = payload.get("novelty_candidates", [])
    if not isinstance(novelty_raw, list):
        raise ReviewError("novelty_candidates must be a list")
    if len(novelty_raw) > MAX_NOVELTY_CANDIDATES:
        raise ReviewError("too many novelty candidates")
    novelty = tuple(
        _clean_text(item, "novelty candidate", MAX_NOVELTY_LABEL)
        for item in novelty_raw
    )
    if any(not item for item in novelty):
        raise ReviewError("novelty candidates cannot be empty")
    if len({item.casefold() for item in novelty}) != len(novelty):
        raise ReviewError("novelty candidates contain duplicates")
    if status == "rejected" and (observations or novelty):
        raise ReviewError(
            "rejected target slices cannot retain observations or novelty")
    note = _clean_text(payload.get("review_note"), "review_note", MAX_REVIEW_NOTE)
    return ReviewDecision(
        unit_id=unit_id,
        source_report_id=source_report_id,
        source_fingerprint=source_hash,
        classifier_version=CLASSIFIER_VERSION,
        status=status,
        target_family=family or None,
        target_variant=variant or None,
        observations=observations,
        novelty_candidates=novelty,
        review_note=note or None,
        reviewed_at=now if now is not None else time.time(),
    )


def _observation_json(observation: CodedObservation) -> dict:
    payload = asdict(observation)
    payload["suspected_layers"] = list(payload["suspected_layers"])
    payload["qualifiers"] = list(payload["qualifiers"])
    return payload


def _stored_row(row: sqlite3.Row) -> dict:
    result = dict(row)
    result["observations"] = json.loads(result.pop("observations_json"))
    result["novelty_candidates"] = json.loads(result.pop("novelty_json"))
    return result


def load_review_decisions_read_only(path: str | Path) -> dict[str, dict]:
    """Load the current review units without creating or migrating anything."""
    database = Path(path).resolve()
    if not database.exists():
        raise ReviewError(f"review database does not exist: {database}")
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='classifier_review_units'"
        ).fetchone()
        if table is None:
            raise ReviewError("review database has no current review-unit table")
        rows = connection.execute(
            "SELECT unit_id,source_report_id,source_fingerprint,"
            "classifier_version,status,target_family,target_variant,"
            "observations_json,novelty_json,review_note,reviewed_at "
            "FROM classifier_review_units ORDER BY reviewed_at"
        ).fetchall()
        return {row["unit_id"]: _stored_row(row) for row in rows}
    finally:
        connection.close()


class ReviewStore:
    def __init__(self, path: str):
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(REVIEW_SCHEMA)
        self._migrate_legacy_reviews()

    def _migrate_legacy_reviews(self) -> None:
        """Preserve decisions made before report-by-target review existed."""
        table = self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='classifier_reviews'"
        ).fetchone()
        if table is None:
            return
        columns = {
            row[1] for row in self.db.execute(
                "PRAGMA table_info(classifier_reviews)")
        }
        required = {
            "report_id", "source_fingerprint", "classifier_version", "status",
            "target_family", "target_variant", "observations_json",
            "novelty_json", "review_note", "reviewed_at",
        }
        if not required <= columns:
            return
        rows = self.db.execute(
            "SELECT report_id,source_fingerprint,classifier_version,status,"
            "target_family,target_variant,observations_json,novelty_json,"
            "review_note,reviewed_at FROM classifier_reviews"
        ).fetchall()
        for row in rows:
            unit_id = review_unit_id(
                row["report_id"], row["target_family"], row["target_variant"])
            self.db.execute(
                "INSERT OR IGNORE INTO classifier_review_units("
                "unit_id,source_report_id,source_fingerprint,classifier_version,"
                "status,target_family,target_variant,observations_json,"
                "novelty_json,review_note,reviewed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    unit_id, row["report_id"], row["source_fingerprint"],
                    row["classifier_version"], row["status"],
                    row["target_family"], row["target_variant"],
                    row["observations_json"], row["novelty_json"],
                    row["review_note"], row["reviewed_at"],
                ),
            )
        self.db.commit()

    def close(self) -> None:
        if self.db is not None:
            self.db.close()
            self.db = None

    def __enter__(self) -> "ReviewStore":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def put(self, decision: ReviewDecision) -> None:
        observations_json = json.dumps(
            [_observation_json(item) for item in decision.observations],
            separators=(",", ":"),
            sort_keys=True,
        )
        novelty_json = json.dumps(
            decision.novelty_candidates,
            separators=(",", ":"),
        )
        self.db.execute(
            "INSERT INTO classifier_review_units("
            "unit_id,source_report_id,source_fingerprint,classifier_version,status,"
            "target_family,target_variant,observations_json,novelty_json,"
            "review_note,reviewed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(unit_id) DO UPDATE SET "
            "source_report_id=excluded.source_report_id,"
            "source_fingerprint=excluded.source_fingerprint,"
            "classifier_version=excluded.classifier_version,"
            "status=excluded.status,target_family=excluded.target_family,"
            "target_variant=excluded.target_variant,"
            "observations_json=excluded.observations_json,"
            "novelty_json=excluded.novelty_json,"
            "review_note=excluded.review_note,reviewed_at=excluded.reviewed_at",
            (
                decision.unit_id,
                decision.source_report_id,
                decision.source_fingerprint,
                decision.classifier_version,
                decision.status,
                decision.target_family,
                decision.target_variant,
                observations_json,
                novelty_json,
                decision.review_note,
                decision.reviewed_at,
            ),
        )
        self.db.commit()

    def get(self, unit_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT unit_id,source_report_id,source_fingerprint,"
            "classifier_version,status,"
            "target_family,target_variant,observations_json,novelty_json,"
            "review_note,reviewed_at FROM classifier_review_units WHERE unit_id=?",
            (unit_id,),
        ).fetchone()
        if row is None:
            return None
        return _stored_row(row)

    def all(self) -> dict[str, dict]:
        rows = self.db.execute(
            "SELECT unit_id FROM classifier_review_units ORDER BY reviewed_at"
        ).fetchall()
        return {row["unit_id"]: self.get(row["unit_id"]) for row in rows}
