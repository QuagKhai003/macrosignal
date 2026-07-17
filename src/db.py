"""SQLite storage — signals.db schema + connection.

@context  The machine's only persistence (build plan Phase 0): 4 tables —
          series (the signal registry mirror), observations (every raw number
          ever fetched, append-only), states (weekly state machine output),
          journal (the honesty ledger).
@done     Idempotent schema creation; connect(); both data_date AND pub_date
          on observations (as-of discipline, tech spec Part 1).
@todo     Phase 1: insert helpers for fetchers (INSERT OR IGNORE on
          observations); as-of query helpers for the replay engine.
@limits   Append-only observations: rows are never updated or deleted.
          States/journal values come only from the frozen formulas (Golden Rule).
@affects  weekly_run.py (0.4); all Phase 1+ fetchers and the state engine.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path("signals.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    series_id      TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    url            TEXT NOT NULL,
    fetch_schedule TEXT NOT NULL,
    window_type    TEXT NOT NULL,
    notes          TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS observations (
    series_id TEXT NOT NULL REFERENCES series(series_id),
    data_date TEXT NOT NULL,  -- ISO date the value refers to
    pub_date  TEXT NOT NULL,  -- ISO date the value became publicly known
    value     REAL NOT NULL,
    PRIMARY KEY (series_id, data_date, pub_date)
);

CREATE TABLE IF NOT EXISTS states (
    market_id   TEXT NOT NULL,
    week        TEXT NOT NULL,  -- ISO week, e.g. 2026-W29
    state       TEXT NOT NULL CHECK (state IN
                    ('NEUTRAL', 'EARLY', 'CONFIRMED', 'CROWDED', 'BROKEN')),
    age_weeks   INTEGER NOT NULL CHECK (age_weeks >= 0),
    scores_json TEXT NOT NULL,
    PRIMARY KEY (market_id, week)
);

CREATE TABLE IF NOT EXISTS journal (
    journal_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    date           TEXT NOT NULL,
    market_id      TEXT,  -- NULL for machine-wide events (runs, weather)
    event_type     TEXT NOT NULL CHECK (event_type IN
                       ('run', 'state_change', 'flag', 'action_taken', 'veto')),
    detail         TEXT NOT NULL,
    price_at_event REAL   -- NULL when no market price applies
);
"""


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables if absent. Safe to call on every run."""
    conn.executescript(_SCHEMA)
    conn.commit()
