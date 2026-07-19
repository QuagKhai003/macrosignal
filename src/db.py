"""SQLite storage — signals.db schema + connection.

@context  The machine's only persistence: series (the signal registry
          mirror), observations (every raw number ever fetched, append-only),
          states (weekly state machine output), journal (the honesty ledger),
          headlines (every ingested headline + its audit-logged label —
          Phase 4, the one caged LLM surface), insider_buys (Form-4
          open-market purchases in the F13 theme universe — semis batch).
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

CREATE TABLE IF NOT EXISTS headlines (
    headline_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    theme          TEXT NOT NULL,  -- market id (gold, wti, ...)
    seen_date      TEXT NOT NULL,  -- ISO date GDELT saw the article
    title          TEXT NOT NULL,
    source_url     TEXT NOT NULL DEFAULT '',
    label          TEXT CHECK (label IN
                       ('excited', 'scared', 'neutral', 'error')),
    model          TEXT,           -- stamped at classification time
    prompt_version TEXT,
    UNIQUE (theme, title, seen_date)
);

CREATE TABLE IF NOT EXISTS weekly_readouts (
    week         TEXT PRIMARY KEY,  -- ISO week, e.g. 2026-W29
    world_json   TEXT NOT NULL,     -- rendered world-picture lines (list)
    forward_json TEXT NOT NULL,     -- {market: rendered base-rate sentence}
    sim_json     TEXT NOT NULL      -- {market: rendered simulation sentence}
);

CREATE TABLE IF NOT EXISTS insider_buys (
    ticker      TEXT NOT NULL,  -- issuer, from the F13 universe config
    buyer       TEXT NOT NULL,  -- reporting owner name as filed
    trans_date  TEXT NOT NULL,  -- ISO transaction date
    filing_date TEXT NOT NULL,  -- ISO EDGAR filing date (true as-of)
    accession   TEXT NOT NULL,  -- source Form 4 accession number
    role        TEXT NOT NULL DEFAULT '',  -- officer title (Tier-2: CFO weight)
    is_10b51    INTEGER NOT NULL DEFAULT 0, -- pre-planned trade flag
    PRIMARY KEY (accession, buyer, trans_date)
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


# columns added to existing tables after their first ship — applied
# idempotently so a live db self-heals without a rebuild (append-only safe:
# ADD COLUMN never drops data). (table, column, definition).
_MIGRATIONS = [
    ("insider_buys", "role", "TEXT NOT NULL DEFAULT ''"),
    ("insider_buys", "is_10b51", "INTEGER NOT NULL DEFAULT 0"),
]


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables if absent, then apply additive migrations. Safe to
    call on every run."""
    conn.executescript(_SCHEMA)
    for table, column, definition in _MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    conn.commit()
