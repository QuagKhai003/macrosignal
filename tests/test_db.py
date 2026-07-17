"""Schema tests for signals.db.

@context  Batch 0.2 acceptance: idempotent init, the 4 tables exist with the
          as-of columns, and the schema's own constraints hold.
@done     Idempotency, table/column presence, append-only uniqueness on
          observations, state CHECK, journal run-row insert.
@todo     —
@limits   Offline, deterministic; uses tmp_path, never the real signals.db.
@affects  src/db.py.
"""

import sqlite3

import pytest

from src import db


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    db.init_db(conn)
    yield conn
    conn.close()


def test_init_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    db.init_db(conn)
    db.init_db(conn)  # second run must not raise or destroy
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"series", "observations", "states", "journal"} <= tables
    conn.close()


def test_observations_have_both_dates(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(observations)")}
    assert {"series_id", "data_date", "pub_date", "value"} == cols


def test_observations_reject_duplicates(conn):
    conn.execute("INSERT INTO series VALUES ('gold_cot', 'CFTC', 'u', 'weekly', 'rolling3y', '')")
    row = ("gold_cot", "2026-07-14", "2026-07-17", 123456.0)
    conn.execute("INSERT INTO observations VALUES (?, ?, ?, ?)", row)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO observations VALUES (?, ?, ?, ?)", row)


def test_states_reject_unknown_state(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO states VALUES ('gold', '2026-W29', 'MOONING', 0, '{}')")


def test_journal_accepts_run_row(conn):
    conn.execute(
        "INSERT INTO journal (date, market_id, event_type, detail, price_at_event)"
        " VALUES ('2026-07-18', NULL, 'run', 'run complete', NULL)")
    n = conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0]
    assert n == 1
