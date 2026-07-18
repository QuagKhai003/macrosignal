"""Alarm budget + veto tests.

@context  Batch 6.1: the rolling-year actionable count vs the §16 budget, the
          window boundary (events older than 52 weeks excluded), and veto
          tallying.
@done     Under/over budget, window exclusion, veto store + rolling count.
@todo     —
@limits   Offline; synthetic states/journal.
@affects  src/alarms.py, log_veto.py.
"""

import datetime as dt

import pytest

import log_veto
from src import alarms, db

AS_OF = dt.date(2026, 7, 18)


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def put_states(conn, market, seq, first=dt.date(2025, 8, 2)):
    """seq: states on consecutive ISO weeks starting at `first` (ISO-week
    labels computed exactly as alarms does)."""
    for i, state in enumerate(seq):
        day = first + dt.timedelta(weeks=i)
        iso = day.isocalendar()
        conn.execute("INSERT INTO states VALUES (?, ?, ?, 0, '{}')",
                     (market, f"{iso.year}-W{iso.week:02d}", state))


def test_under_budget_no_banner(conn):
    # 3 actionable entries within the trailing year -> under 20
    put_states(conn, "gold",
               ["NEUTRAL", "CONFIRMED", "NEUTRAL", "BROKEN", "NEUTRAL",
                "CONFIRMED"])
    r = alarms.alarm_budget(conn, AS_OF)
    assert r["events"] == 3 and r["over_budget"] is False
    assert r["banner"] is None


def test_over_budget_raises_banner(conn):
    # alternate NEUTRAL/CONFIRMED across ~46 weeks ending inside the window
    # -> ~23 entries into CONFIRMED (all within the trailing 52 weeks)
    put_states(conn, "wti", ["NEUTRAL", "CONFIRMED"] * 23)
    r = alarms.alarm_budget(conn, AS_OF)
    assert r["events"] > 20 and r["over_budget"] is True
    assert "trust the machine LESS" in r["banner"]


def test_events_older_than_a_year_excluded(conn):
    # actionable entries in early 2024 (>52 weeks before AS_OF) must not count
    put_states(conn, "gold", ["NEUTRAL", "CONFIRMED", "NEUTRAL", "BROKEN"],
               first=dt.date(2024, 3, 4))
    r = alarms.alarm_budget(conn, AS_OF)
    assert r["events"] == 0


def test_veto_store_and_rolling_count(conn, tmp_path):
    db_path = tmp_path / "t.db"
    msg = log_veto.store("gold", 4023.0, "FOMO on the rally",
                         db_path=db_path, today=AS_OF)
    assert "veto logged: gold" in msg
    conn2 = db.connect(db_path)
    row = conn2.execute("SELECT market_id, event_type, detail, price_at_event"
                        " FROM journal WHERE event_type = 'veto'").fetchone()
    assert row[0] == "gold" and row[3] == 4023.0
    assert "machine said no" in row[2]
    assert alarms.veto_stats(conn2, AS_OF)["vetoes_rolling_year"] == 1
    conn2.close()


def test_veto_older_than_year_not_counted(conn):
    conn.execute("INSERT INTO journal (date, market_id, event_type, detail,"
                 " price_at_event) VALUES ('2024-01-01', 'gold', 'veto',"
                 " 'old', 1.0)")
    conn.commit()
    assert alarms.veto_stats(conn, AS_OF)["vetoes_rolling_year"] == 0
