"""Persistent state for the Barometer. SQLite, stdlib, no ceremony."""
from __future__ import annotations
import hashlib
import sqlite3
from .detect import Complaint, CanaryReading, ProviderEvent
from .probes import CollectionRun

SCHEMA = """
CREATE TABLE IF NOT EXISTS complaints(
  id TEXT PRIMARY KEY, ts REAL, source TEXT, model TEXT,
  text TEXT, url TEXT, seed_url TEXT, variant TEXT);
CREATE TABLE IF NOT EXISTS readings(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, model TEXT,
  logprobs TEXT, fingerprint TEXT);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, model TEXT,
  kind TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS tap_state(
  key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tap_usage(
  day TEXT, source TEXT, units INTEGER NOT NULL,
  PRIMARY KEY(day, source));
CREATE TABLE IF NOT EXISTS collection_runs(
  run_id TEXT PRIMARY KEY, source TEXT NOT NULL, lane TEXT NOT NULL,
  query_id TEXT NOT NULL, query_version INTEGER NOT NULL,
  started_at REAL NOT NULL, completed_at REAL NOT NULL,
  returned_candidates INTEGER NOT NULL, retained_candidates INTEGER NOT NULL,
  item_cap INTEGER, saturated INTEGER NOT NULL,
  cost_units INTEGER, cost_usd_upper_bound REAL, frame_note TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS complaint_provenance(
  complaint_id TEXT NOT NULL, run_id TEXT NOT NULL, result_rank INTEGER,
  PRIMARY KEY(complaint_id, run_id));
CREATE INDEX IF NOT EXISTS ix_c_model_ts ON complaints(model, ts);
CREATE INDEX IF NOT EXISTS ix_r_model_ts ON readings(model, ts);
CREATE INDEX IF NOT EXISTS ix_collection_query
  ON collection_runs(source, lane, query_id, query_version);
CREATE INDEX IF NOT EXISTS ix_provenance_run ON complaint_provenance(run_id);
"""

def _cid(c: Complaint) -> str:
    basis = c.url or f"{c.source}|{c.ts:.0f}|{c.text[:120]}"
    return hashlib.sha256(basis.encode()).hexdigest()[:24]

class Store:
    def __init__(self, path: str = "barometer.db"):
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        columns = {
            row[1] for row in self.db.execute("PRAGMA table_info(complaints)")
        }
        if "variant" not in columns:
            self.db.execute("ALTER TABLE complaints ADD COLUMN variant TEXT")
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS ix_c_variant_ts "
            "ON complaints(variant, ts)"
        )
        self.db.commit()

    def close(self) -> None:
        """Close the SQLite connection and release its filesystem handle."""
        if self.db is not None:
            self.db.close()
            self.db = None

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    # -------- complaints --------
    def _collection_run_values(self, run: CollectionRun) -> tuple:
        return (
            run.run_id, run.source, run.lane, run.query_id, run.query_version,
            run.started_at, run.completed_at, run.returned_candidates,
            run.retained_candidates, run.item_cap, int(run.saturated),
            run.cost_units, run.cost_usd_upper_bound, run.frame_note,
        )

    def _insert_collection_run(self, run: CollectionRun) -> None:
        values = self._collection_run_values(run)
        existing = self.db.execute(
            "SELECT run_id,source,lane,query_id,query_version,started_at,"
            "completed_at,returned_candidates,retained_candidates,item_cap,"
            "saturated,cost_units,cost_usd_upper_bound,frame_note "
            "FROM collection_runs WHERE run_id=?", (run.run_id,),
        ).fetchone()
        if existing is not None and tuple(existing) != values:
            raise ValueError("collection run ID already has different metadata")
        if existing is None:
            self.db.execute(
                "INSERT INTO collection_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                values,
            )

    def record_collection_run(self, run: CollectionRun) -> None:
        """Record a query receipt; this method itself performs no collection."""
        self._insert_collection_run(run)
        self.db.commit()

    def add_complaints(
            self, cs: list[Complaint], collection_run: CollectionRun | None = None,
            result_ranks: list[int | None] | None = None) -> int:
        """Insert reports and optional query provenance, deduplicating both."""
        if result_ranks is not None and len(result_ranks) != len(cs):
            raise ValueError("one result rank is required per complaint")
        if result_ranks is not None and any(
                rank is not None and (not isinstance(rank, int) or rank < 1)
                for rank in result_ranks):
            raise ValueError("result ranks must be positive or null")
        if collection_run is not None:
            if collection_run.retained_candidates != len(cs):
                raise ValueError(
                    "collection retained_candidates must match supplied reports")
        try:
            if collection_run is not None:
                self._insert_collection_run(collection_run)
            new = 0
            for index, c in enumerate(cs):
                complaint_id = _cid(c)
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO complaints"
                    "(id,ts,source,model,text,url,seed_url,variant) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (complaint_id, c.ts, c.source, c.model, c.text, c.url,
                     c.seed_url, c.variant))
                new += cur.rowcount
                if collection_run is not None:
                    rank = (
                        result_ranks[index]
                        if result_ranks is not None else None)
                    self.db.execute(
                        "INSERT OR IGNORE INTO complaint_provenance"
                        "(complaint_id,run_id,result_rank) VALUES (?,?,?)",
                        (complaint_id, collection_run.run_id, rank),
                    )
            self.db.commit()
            return new
        except Exception:
            self.db.rollback()
            raise

    def collection_run_records(self) -> list[dict]:
        columns = (
            "run_id", "source", "lane", "query_id", "query_version",
            "started_at", "completed_at", "returned_candidates",
            "retained_candidates", "item_cap", "saturated", "cost_units",
            "cost_usd_upper_bound", "frame_note",
        )
        rows = self.db.execute(
            "SELECT " + ",".join(columns) + " FROM collection_runs "
            "ORDER BY started_at,run_id"
        ).fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def complaint_provenance(self, complaint_id: str | None = None) -> list[dict]:
        query = (
            "SELECT p.complaint_id,p.run_id,p.result_rank,r.source,r.lane,"
            "r.query_id,r.query_version FROM complaint_provenance p "
            "JOIN collection_runs r ON r.run_id=p.run_id"
        )
        args: tuple = ()
        if complaint_id is not None:
            query += " WHERE p.complaint_id=?"
            args = (complaint_id,)
        columns = (
            "complaint_id", "run_id", "result_rank", "source", "lane",
            "query_id", "query_version",
        )
        rows = self.db.execute(query + " ORDER BY p.run_id", args).fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def complaints(self, model: str | None = None,
                   since: float = 0.0) -> list[Complaint]:
        q = ("SELECT ts,source,model,text,url,seed_url,variant "
             "FROM complaints WHERE ts>=?")
        args: list = [since]
        if model: q += " AND model=?"; args.append(model)
        rows = self.db.execute(q + " ORDER BY ts", args).fetchall()
        return [Complaint(*r) for r in rows]

    # -------- readings --------
    def add_reading(self, r: CanaryReading) -> None:
        self.db.execute("INSERT INTO readings(ts,model,logprobs,fingerprint) "
                        "VALUES (?,?,?,?)",
                        (r.ts, r.model, ",".join(f"{x:.6f}" for x in r.logprobs),
                         r.fingerprint))
        self.db.commit()

    def readings(self, model: str) -> list[CanaryReading]:
        rows = self.db.execute(
            "SELECT ts,model,logprobs,fingerprint FROM readings "
            "WHERE model=? ORDER BY ts", (model,)).fetchall()
        return [CanaryReading(r[0], r[1],
                              [float(x) for x in r[2].split(",") if x], r[3])
                for r in rows]

    def last_reading_ts(self, model: str) -> float | None:
        row = self.db.execute("SELECT MAX(ts) FROM readings WHERE model=?",
                              (model,)).fetchone()
        return row[0]

    # -------- events --------
    def add_event(self, e: ProviderEvent) -> None:
        self.db.execute("INSERT INTO events(ts,model,kind,note) VALUES (?,?,?,?)",
                        (e.ts, e.model, e.kind, e.note))
        self.db.commit()

    def events(self, model: str | None = None) -> list[ProviderEvent]:
        q, args = "SELECT ts,model,kind,note FROM events", []
        if model: q += " WHERE model=?"; args.append(model)
        return [ProviderEvent(*r) for r in
                self.db.execute(q + " ORDER BY ts", args).fetchall()]

    def models(self) -> list[str]:
        return [r[0] for r in
                self.db.execute("SELECT DISTINCT model FROM complaints").fetchall()]

    def prune_complaints(self, before: float) -> int:
        """Delete raw reports older than a retention cutoff."""
        cur = self.db.execute("DELETE FROM complaints WHERE ts < ?", (before,))
        self.db.commit()
        return cur.rowcount

    # -------- metered tap state --------
    def tap_state(self, key: str) -> str | None:
        row = self.db.execute(
            "SELECT value FROM tap_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_tap_state(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO tap_state(key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self.db.commit()

    def tap_usage(self, day: str, source: str) -> int:
        row = self.db.execute(
            "SELECT units FROM tap_usage WHERE day=? AND source=?",
            (day, source),
        ).fetchone()
        return int(row[0]) if row else 0

    def reserve_tap_usage(
            self, day: str, source: str, units: int, limit: int) -> bool:
        """Atomically reserve units without crossing a daily hard limit."""
        if units < 0 or limit < 0:
            raise ValueError("tap usage units and limit must be non-negative")
        try:
            self.db.execute("BEGIN IMMEDIATE")
            used = self.tap_usage(day, source)
            if used + units > limit:
                self.db.rollback()
                return False
            self.db.execute(
                "INSERT INTO tap_usage(day,source,units) VALUES (?,?,?) "
                "ON CONFLICT(day,source) DO UPDATE SET units=excluded.units",
                (day, source, used + units),
            )
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise

    def adjust_tap_usage(self, day: str, source: str, delta: int) -> None:
        """Adjust a reservation after the source reveals actual usage."""
        try:
            self.db.execute("BEGIN IMMEDIATE")
            used = self.tap_usage(day, source)
            adjusted = used + delta
            if adjusted < 0:
                raise ValueError("tap usage cannot become negative")
            self.db.execute(
                "INSERT INTO tap_usage(day,source,units) VALUES (?,?,?) "
                "ON CONFLICT(day,source) DO UPDATE SET units=excluded.units",
                (day, source, adjusted),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
