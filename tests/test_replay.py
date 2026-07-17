"""Replay harness tests — weekly advance + flip counting.

@context  Batch 2.5: the loop must lay one states row per market per week and
          the analyzer must count transitions, not weeks.
@done     4-week synthetic replay row counts; flip/timeline arithmetic on a
          hand-built states table.
@todo     —
@limits   Offline; the empirical acceptance runs against the real db (results
          recorded in the ADR).
@affects  src/replay.py.
"""

import datetime as dt

from src import db, replay


def test_run_lays_weekly_rows(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    result = replay.run(conn, dt.date(2026, 6, 6), dt.date(2026, 6, 27))
    assert all(m["weeks"] == 4 for m in result.values())
    n = conn.execute("SELECT COUNT(*) FROM states").fetchone()[0]
    assert n == 4 * 5  # 4 weeks x 5 markets, all NEUTRAL (empty db)
    assert all(m["flips"] == 0 for m in result.values())
    conn.close()


def test_analyze_counts_flips_and_condenses(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    history = [("2026-W01", "NEUTRAL"), ("2026-W02", "NEUTRAL"),
               ("2026-W03", "CONFIRMED"), ("2026-W04", "CONFIRMED"),
               ("2026-W05", "BROKEN"), ("2026-W06", "NEUTRAL")]
    for week, state in history:
        conn.execute("INSERT INTO states VALUES ('gold', ?, ?, 0, '{}')",
                     (week, state))
    result = replay.analyze(conn, "2026-W01", "2026-W06")
    gold = result["gold"]
    assert gold["flips"] == 3
    assert gold["timeline"] == [
        ("NEUTRAL", "2026-W01", "2026-W02"),
        ("CONFIRMED", "2026-W03", "2026-W04"),
        ("BROKEN", "2026-W05", "2026-W05"),
        ("NEUTRAL", "2026-W06", "2026-W06")]
    conn.close()
