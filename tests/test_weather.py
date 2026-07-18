"""Weather light tests — the F11 points table.

@context  Batch 3.3: every gauge threshold, the points→light mapping, missing-
          gauge honesty, and the July 2026 hand-derived reading as a fixture.
@done     Those scenarios over the pure evaluate(); light() smoke via a tiny
          db with only a manual cash entry.
@todo     —
@limits   Offline, pure.
@affects  src/weather.py.
"""

import manual_entry
from src import db, weather


def readouts(cash=None, val=None, spread=None, falling=None):
    return {"manager_cash": cash, "valuation_pct": val, "spread_pct": spread,
            "liquidity_falling": falling}


def test_all_green():
    r = weather.evaluate(readouts(cash=5.0, val=50.0, spread=40.0, falling=False))
    assert (r["light"], r["points"]) == ("GREEN", 0)


def test_one_point_is_yellow():
    r = weather.evaluate(readouts(cash=5.0, val=95.0, spread=40.0, falling=False))
    assert (r["light"], r["points"]) == ("YELLOW", 1)


def test_july_2026_hand_reading_is_red():
    # product doc §12: cash 3.6 red + valuation record red = RED
    r = weather.evaluate(readouts(cash=3.6, val=100.0, spread=10.4, falling=False))
    assert (r["light"], r["points"]) == ("RED", 2)
    assert r["gauges"]["manager_cash"]["red"] is True
    assert r["gauges"]["valuation_pct"]["red"] is True
    assert r["gauges"]["spread_pct"]["red"] is False


def test_all_four_red():
    r = weather.evaluate(readouts(cash=3.0, val=99.0, spread=95.0, falling=True))
    assert (r["light"], r["points"]) == ("RED", 4)


def test_thresholds_are_exclusive():
    # exactly at a threshold is NOT red (red needs < or > per F11)
    r = weather.evaluate(readouts(cash=4.0, val=90.0, spread=80.0, falling=False))
    assert r["points"] == 0


def test_missing_gauges_contribute_zero_and_report_none():
    r = weather.evaluate(readouts(cash=3.0))
    assert (r["light"], r["points"]) == ("YELLOW", 1)
    assert r["gauges"]["valuation_pct"]["red"] is None


def test_light_reads_db_as_of(tmp_path):
    db_path = tmp_path / "t.db"
    manual_entry.store("manager_cash", 3.6, "2026-07-15", db_path=db_path)
    conn = db.connect(db_path)
    r = weather.light(conn, "2026-07-18")
    assert r["gauges"]["manager_cash"]["value"] == 3.6
    assert r["light"] == "YELLOW"  # only the cash gauge answers: 1 point
    before = weather.light(conn, "2026-07-01")  # entry not yet published
    assert before["gauges"]["manager_cash"]["value"] is None
    conn.close()
