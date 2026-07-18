"""Health check tests (batch 6.4).

@context  --health = is the weekly habit alive; pure db reads, exit code
          drives the scheduled task's visibility.
@done     Fresh OK / stale FAIL / never-ran FAIL; failure-count parse.
@todo     —
@limits   Offline.
@affects  src/health.py, weekly_run.py --health.
"""

import datetime as dt

import pytest

from src import db, health


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def put_run(conn, date, detail="run complete - 5 new observations,"
                               " 2 fetch failures"):
    conn.execute("INSERT INTO journal (date, market_id, event_type, detail,"
                 " price_at_event) VALUES (?, NULL, 'run', ?, NULL)",
                 (date, detail))


def test_fresh_run_is_healthy(conn, capsys):
    put_run(conn, "2026-07-18")
    assert health.check(conn, today=dt.date(2026, 7, 19)) == 0
    out = capsys.readouterr().out
    assert "HEALTH: OK" in out and "2 fetch failures" in out


def test_stale_run_fails(conn, capsys):
    put_run(conn, "2026-07-01")
    assert health.check(conn, today=dt.date(2026, 7, 19)) == 1
    assert "STALE" in capsys.readouterr().out


def test_never_ran_fails(conn, capsys):
    assert health.check(conn, today=dt.date(2026, 7, 19)) == 1
    assert "no run" in capsys.readouterr().out


def test_failure_count_parse():
    assert health._failures("run complete - 3 new observations,"
                            " 14 fetch failures") == 14
    assert health._failures("weird detail") == 0
