"""World picture + forward base-rate tests (the honest forecast batch).

@context  worldview.lines = deterministic sentences from the weekly
          readouts (numbers + crowd + actors + disagreements);
          forward.base_rates = per-state forward-return distributions from
          the replay record. History, not prophecy.
@done     World sentences per side, missing-side silence, disagreement
          callouts, overall line per weather; base-rate hand-check,
          MIN_SAMPLE gate, sentence rendering; report sections.
@todo     —
@limits   Offline; synthetic states/prices.
@affects  src/worldview.py, src/forward.py, src/report.py.
"""

import datetime as dt

import pytest

from src import db, forward, report, worldview


def test_world_lines_full_picture():
    summary = {"net_liquidity_pct": 66.0, "net_liquidity_falling": False,
               "us_dollar_pct": 71.0, "market_valuation_pct": 99.9,
               "weather_manager_cash": 3.6, "credit_spread_pct": 10.0}
    results = {"gold": {"party_pct": 85.0, "news": "loud_greedy"},
               "wti": {"party_pct": 15.0, "news": "quiet"},
               "corn": {"party_pct": None}}
    ledger = [{"name": "soros", "total": 9e9, "prior": 8e9},
              {"name": "pershing", "total": 12e9, "prior": 14e9},
              {"name": "duquesne", "total": 3e9, "prior": 4e9}]
    lines = worldview.lines(summary, "RED", results, whale_ledger=ledger,
                            insider_flags={"NVDA": True})
    text = "\n".join(lines)
    assert "money tide is rising" in text
    assert "dollar is strong" in text
    assert "priced near records" in text
    assert "3.6% spare cash" in text
    assert "Credit markets are calm" in text
    assert "Crowded trades: Gold." in text
    assert "Abandoned trades: Oil (WTI)." in text
    assert "talking about: Gold" in text
    assert "cutting: 1 of 3" in text
    assert "cluster-buying at: NVDA" in text
    assert "whales cut exposure" in text  # the disagreement callout
    assert "storm conditions" in text


def test_world_lines_missing_sides_stay_silent():
    lines = worldview.lines({}, "GREEN", {})
    assert lines == ["Overall: calm — normal operation."]


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    conn.execute("INSERT INTO series VALUES ('price_gold','s','u','daily',"
                 "'sma200','')")
    yield conn
    conn.close()


def seed_state_history(conn, n_weeks, state="CONFIRMED", weekly_gain=0.01):
    """n_weeks of one state with steadily compounding weekly prices."""
    day = dt.date(2015, 1, 7)
    price = 100.0
    for i in range(n_weeks):
        week = f"{day.isocalendar().year}-W{day.isocalendar().week:02d}"
        conn.execute("INSERT OR IGNORE INTO states VALUES ('gold', ?, ?, 0,"
                     " '{}')", (week, state))
        conn.execute("INSERT INTO observations VALUES ('price_gold', ?, ?,"
                     " ?)", (day.isoformat(), day.isoformat(), price))
        price *= 1.0 + weekly_gain
        day += dt.timedelta(weeks=1)


def test_base_rates_hand_check(conn):
    seed_state_history(conn, 80, weekly_gain=0.01)
    rates = forward.base_rates(conn, "2026-07-19", horizon=13)
    stats = rates["gold"]["CONFIRMED"]
    # every 13-week forward return of a steady +1%/wk series = 1.01^13 - 1
    expected = (1.01 ** 13 - 1) * 100
    assert stats["n"] == 80 - 13
    assert stats["mean"] == pytest.approx(expected, abs=0.01)
    assert stats["worst"] == pytest.approx(expected, abs=0.01)


def test_base_rates_min_sample_gate(conn):
    seed_state_history(conn, 40)  # 40 - 13 = 27 samples < 30
    rates = forward.base_rates(conn, "2026-07-19", horizon=13)
    assert rates["gold"]["CONFIRMED"] is None
    assert "Not enough alike-weeks" in forward.sentence(None)


def test_forward_sentence_renders():
    s = forward.sentence({"n": 200, "mean": 2.8, "p25": -2.0, "p75": 7.1,
                          "worst": -15.3})
    assert "averaged +2.8%" in s and "-2.0% and +7.1%" in s
    assert "worst seen was -15.3%" in s and "200 alike-weeks" in s


def test_report_renders_world_and_forward():
    results = {"gold": {"state": "CONFIRMED", "age_weeks": 3,
                        "size_fraction": 0.1, "engine": True, "alive": True,
                        "party_pct": 50.0, "momentum": 1, "news": "quiet",
                        "scared_and_abandoned": False}}
    text = report.build(results, {"gold": "CONFIRMED"}, "2026-W29",
                        world_lines=["The money tide is rising."],
                        forward_stats={"gold": {"CONFIRMED": {
                            "n": 100, "mean": 2.0, "p25": -1.0, "p75": 5.0,
                            "worst": -12.0}}})
    assert "— The world right now —" in text
    assert "The money tide is rising." in text
    assert "alike-weeks did next" in text and "Gold: From weeks like" in text
