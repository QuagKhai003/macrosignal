"""End-to-end test of the weekly runner (Phase 0 acceptance).

@context  Build plan Phase 0 acceptance check: `weekly_run` executes, writes a
          journal row, and signals.yaml holds 5 admitted entries.
@done     Runner returns 0, prints "run complete", mirrors 5 series rows,
          appends one journal 'run' row per run (two runs → two rows, series
          still 5 — idempotent sync, append-only ledger).
@todo     —
@limits   Offline, deterministic; tmp db, fixed as-of date, real signals.yaml.
@affects  weekly_run.py, src/db.py, src/registry.py, signals.yaml.
"""

import datetime as dt

import weekly_run
from src import db

AS_OF = dt.date(2026, 7, 18)


def test_run_writes_journal_and_series(tmp_path, capsys):
    db_path = tmp_path / "signals.db"
    assert weekly_run.main(db_path=db_path, today=AS_OF) == 0
    assert capsys.readouterr().out.strip() == "run complete"

    conn = db.connect(db_path)
    journal = conn.execute(
        "SELECT date, market_id, event_type FROM journal").fetchall()
    assert journal == [("2026-07-18", None, "run")]
    assert conn.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 5
    conn.close()


def test_second_run_appends_journal_not_series(tmp_path):
    db_path = tmp_path / "signals.db"
    weekly_run.main(db_path=db_path, today=AS_OF)
    weekly_run.main(db_path=db_path, today=AS_OF + dt.timedelta(days=7))

    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 5
    conn.close()


def test_src_package_imports():
    import src  # noqa: F401
