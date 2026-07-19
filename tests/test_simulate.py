"""Monte Carlo forecaster tests (the simulation batch).

@context  Bootstrap paths from same-state weekly returns; seeded per
          (market, state) so the same week always reproduces the same
          numbers — a frozen procedure, not a model.
@done     Determinism, all-positive pool → 100% higher, hand-checkable
          single-value pool, MIN_SAMPLE gate, sentence, report section.
@todo     —
@limits   Offline; synthetic states/prices.
@affects  src/simulate.py, src/report.py.
"""

import datetime as dt

import pytest

from src import db, report, simulate


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    conn.execute("INSERT INTO series VALUES ('price_gold','s','u','daily',"
                 "'sma200','')")
    yield conn
    conn.close()


def seed(conn, n_weeks, weekly_gain):
    day = dt.date(2015, 1, 7)
    price = 100.0
    for i in range(n_weeks):
        week = f"{day.isocalendar().year}-W{day.isocalendar().week:02d}"
        conn.execute("INSERT OR IGNORE INTO states VALUES ('gold', ?,"
                     " 'CONFIRMED', 0, '{}')", (week,))
        conn.execute("INSERT INTO observations VALUES ('price_gold', ?, ?,"
                     " ?)", (day.isoformat(), day.isoformat(), price))
        price *= 1.0 + weekly_gain
        day += dt.timedelta(weeks=1)


def test_constant_pool_hand_check(conn):
    seed(conn, 60, weekly_gain=0.01)  # every weekly return exactly +1%
    sims = simulate.simulate(conn, "2026-07-19", horizon=13, n_sims=200)
    r = sims["gold"]["CONFIRMED"]
    expected = (1.01 ** 13 - 1) * 100
    assert r["prob_higher"] == 100.0
    assert r["p10"] == pytest.approx(expected, abs=0.01)
    assert r["p90"] == pytest.approx(expected, abs=0.01)
    assert r["n_pool"] == 59


def test_deterministic_across_runs(conn):
    seed(conn, 60, weekly_gain=0.01)
    a = simulate.simulate(conn, "2026-07-19", n_sims=300)
    b = simulate.simulate(conn, "2026-07-19", n_sims=300)
    assert a == b


def test_min_sample_gate(conn):
    seed(conn, 20, weekly_gain=0.01)  # 19 returns < 30
    sims = simulate.simulate(conn, "2026-07-19", n_sims=100)
    assert sims["gold"]["CONFIRMED"] is None
    assert "Too few alike-weeks" in simulate.sentence(None)


def test_sentence_renders():
    s = simulate.sentence({"prob_higher": 63.0, "p10": -6.2, "p50": 2.1,
                           "p90": 9.4, "n_pool": 500})
    assert "63 in 100 ended higher" in s
    assert "typical outcome +2.1%" in s
    assert "worst tenth fell below -6.2%" in s


def test_report_simulation_section():
    results = {"gold": {"state": "CONFIRMED", "age_weeks": 1,
                        "size_fraction": 0.1, "engine": True, "alive": True,
                        "party_pct": 50.0, "momentum": 1, "news": "quiet",
                        "scared_and_abandoned": False}}
    text = report.build(results, {"gold": "CONFIRMED"}, "2026-W29",
                        simulations={"gold": {"CONFIRMED": {
                            "prob_higher": 70.0, "p10": -5.0, "p50": 3.0,
                            "p90": 11.0, "n_pool": 400}}})
    assert "— The simulation" in text and "70 in 100 ended higher" in text
