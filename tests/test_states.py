"""State persistence + sizing tests.

@context  Batch 2.3: sizing table pinned to the spec's own worked example;
          persistence reconstructs hysteresis across runs; state changes
          journal exactly once with scores and price.
@done     F12 table incl. the §13.3 example (whale EARLY, 30 wks, YELLOW =
          3.75%); run_week persistence, change-journaling, same-week re-run
          idempotency, flag survival across weeks.
@todo     —
@limits   Offline; synthetic history via helpers.
@affects  src/states.py, src/engine.py (size_fraction).
"""

import datetime as dt
import json

import pytest

from src import db, engine, states

# ── F12 sizing table ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("state, age, weather, whale, expected", [
    ("CONFIRMED", 0, "GREEN", False, 0.20),    # 1/5 x 1 x 1 x 1
    ("CONFIRMED", 0, "YELLOW", False, 0.10),
    ("CONFIRMED", 30, "GREEN", False, 0.15),   # age 26-52 -> x0.75
    ("CONFIRMED", 52, "GREEN", False, 0.15),   # 52 still in the 0.75 band
    ("CONFIRMED", 53, "GREEN", False, 0.10),   # >52 -> x0.5
    ("CONFIRMED", 10, "RED", False, 0.0),      # weather RED: no new entries
    ("EARLY", 30, "YELLOW", True, 0.0375),     # the §13.3 worked example
    ("EARLY", 10, "GREEN", False, 0.0),        # EARLY without whale: watch only
    ("NEUTRAL", 0, "GREEN", False, 0.0),
    ("CROWDED", 0, "GREEN", False, 0.0),
    ("BROKEN", 0, "GREEN", False, 0.0),
])
def test_size_fraction_table(state, age, weather, whale, expected):
    assert engine.size_fraction(state, age, weather, whale) == pytest.approx(expected)


# ── persistence ──────────────────────────────────────────────────────────────

AS_OF_1 = dt.date(2026, 7, 11)
AS_OF_2 = dt.date(2026, 7, 18)


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    for sid in ("cot_gold", "price_gold"):
        conn.execute("INSERT INTO series VALUES (?, 's', 'u', 'w', 'x', '')",
                     (sid,))
    yield conn
    conn.close()


def seed_gold(conn, party_low=True, price_above=True):
    """160 weekly COT values + 220 daily closes engineered for clear answers."""
    start = dt.date(2023, 1, 3)
    cot = [float(1000 + i) for i in range(159)]
    cot.append(1.0 if party_low else 5000.0)  # last value: bottom or top
    conn.executemany("INSERT INTO observations VALUES (?, ?, ?, ?)",
                     [("cot_gold", (start + dt.timedelta(weeks=i)).isoformat(),
                       (start + dt.timedelta(weeks=i)).isoformat(), v)
                      for i, v in enumerate(cot)])
    pstart = dt.date(2025, 11, 1)
    closes = [100.0] * 219 + [101.0 if price_above else 99.0]
    conn.executemany("INSERT INTO observations VALUES (?, ?, ?, ?)",
                     [("price_gold", (pstart + dt.timedelta(days=i)).isoformat(),
                       (pstart + dt.timedelta(days=i)).isoformat(), v)
                      for i, v in enumerate(closes)])


def test_run_week_persists_all_markets(conn):
    seed_gold(conn)
    results = states.run_week(conn, AS_OF_1)
    assert set(results) == set(states.drivers.MARKETS)
    rows = conn.execute("SELECT COUNT(*) FROM states").fetchone()[0]
    assert rows == 8  # expansion batch 1: 5 spine + silver/copper/natgas
    # gold: engine None (no dfii10 data) -> NEUTRAL, honest
    assert results["gold"]["state"] == "NEUTRAL"
    assert results["gold"]["party_pct"] is not None


def test_rerun_same_week_is_idempotent(conn):
    seed_gold(conn)
    states.run_week(conn, AS_OF_1)
    states.run_week(conn, AS_OF_1)
    assert conn.execute("SELECT COUNT(*) FROM states").fetchone()[0] == 8
    journal = conn.execute("SELECT COUNT(*) FROM journal"
                           " WHERE event_type = 'state_change'").fetchone()[0]
    assert journal == 0  # NEUTRAL everywhere: no changes to journal


def test_flags_survive_across_weeks(conn):
    seed_gold(conn, party_low=True)
    states.run_week(conn, AS_OF_1)
    row = conn.execute("SELECT scores_json FROM states WHERE market_id ="
                       " 'gold'").fetchone()[0]
    assert json.loads(row)["flags"]["party_empty"] is True
    prior = states._prior_state(conn, "gold", states.iso_week(AS_OF_2))
    assert prior.party_empty is True


def test_state_change_journals_once_with_price(conn):
    seed_gold(conn)
    # week 1: force a non-NEUTRAL state by faking a prior CONFIRMED row so
    # the transition CONFIRMED -> NEUTRAL journals
    conn.execute("INSERT INTO states VALUES ('gold', '2026-W27', 'CONFIRMED',"
                 " 4, '{}')")
    states.run_week(conn, AS_OF_1)  # 2026-W28
    rows = conn.execute(
        "SELECT market_id, detail, price_at_event FROM journal"
        " WHERE event_type = 'state_change'").fetchall()
    assert len(rows) == 1
    market, detail, price = rows[0]
    assert market == "gold"
    assert detail.startswith("CONFIRMED -> NEUTRAL |")
    assert price == pytest.approx(101.0)
    # re-run: no duplicate journal row
    states.run_week(conn, AS_OF_1)
    n = conn.execute("SELECT COUNT(*) FROM journal"
                     " WHERE event_type = 'state_change'").fetchone()[0]
    assert n == 1
