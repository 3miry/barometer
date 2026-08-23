"""Persistent state for the Barometer. SQLite, stdlib, no ceremony."""
from __future__ import annotations
import hashlib
import sqlite3
from .detect import Complaint, CanaryReading, ProviderEvent

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
CREATE INDEX IF NOT EXISTS ix_c_model_ts ON complaints(model, ts);
CREATE INDEX IF NOT EXISTS ix_r_model_ts ON readings(model, ts);
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
    def add_complaints(self, cs: list[Complaint]) -> int:
        """Insert, deduplicating on stable id. Returns number actually new."""
        new = 0
        for c in cs:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO complaints"
                "(id,ts,source,model,text,url,seed_url,variant) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (_cid(c), c.ts, c.source, c.model, c.text, c.url, c.seed_url,
                 c.variant))
            new += cur.rowcount
        self.db.commit()
        return new

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
