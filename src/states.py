"""Weekly state persistence — observations in, states + journal out.

@context  The bridge between the pure engine and the db: builds each market's
          weekly observation dict (drivers + party + momentum + news stub),
          steps the engine from the persisted prior state, writes the states
          row, and journals every state CHANGE with the scores that caused it
          (build plan Phase 2 task 5).
@done     run_week(): as-of obs assembly, prior-state reconstruction from
          scores_json (hysteresis flags + dead streak survive restarts),
          INSERT OR REPLACE per (market, ISO week), change-journaling with
          price_at_event, re-run-safe (same week re-run never duplicates
          journal rows). Sizing per F12 stored in scores_json.
@todo     Phase 3: weather stops being the YELLOW stub; Phase 4: news stops
          being "quiet".
@limits   All numbers via src/formulas + src/drivers over pub_date <= as_of.
          News hardcoded "quiet" (Phase 2 stub, per build plan).
@affects  weekly_run.py, the states/journal tables; consumed by the report
          (2.4) and replay (2.5).
"""

import datetime as dt
import json
import sqlite3

from src import drivers, engine, formulas, spine

PARTY_WINDOW = spine.WINDOW_OBS[("rolling3y", "weekly")]
WEATHER_STUB = "YELLOW"  # Phase 3 replaces with the real 4-gauge light


def iso_week(day: dt.date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def run_week(conn: sqlite3.Connection, as_of: dt.date) -> dict:
    """Compute + persist this week's states. Returns {market: state_dict}."""
    week = iso_week(as_of)
    as_of_str = as_of.isoformat()
    engines = drivers.engines(conn, as_of_str)
    results = {}
    for market, series in drivers.MARKETS.items():
        obs = {
            "engine": engines[market]["engine"],
            "alive": engines[market]["alive"],
            "party_pct": formulas.pct_rank(
                spine._values(conn, series["cot"], as_of_str), PARTY_WINDOW),
            "momentum": formulas.sma200_flag(
                spine._values(conn, series["price"], as_of_str)),
            "news": "quiet",  # Phase 2 stub
        }
        prev = _prior_state(conn, market, week)
        new = engine.step(prev, obs)
        size = engine.size_fraction(new.state, new.age_weeks, WEATHER_STUB)
        scores = {**{k: obs[k] for k in
                     ("engine", "alive", "party_pct", "momentum", "news")},
                  "size_fraction": size,
                  "flags": {"party_full": new.party_full,
                            "party_empty": new.party_empty,
                            "dead_weeks": new.dead_weeks}}
        _persist(conn, market, week, new, scores, as_of_str, prev)
        results[market] = {"state": new.state, "age_weeks": new.age_weeks,
                           "size_fraction": size, **obs}
    conn.commit()
    return results


def _prior_state(conn, market: str, current_week: str):
    row = conn.execute(
        "SELECT state, age_weeks, scores_json FROM states"
        " WHERE market_id = ? AND week < ? ORDER BY week DESC LIMIT 1",
        (market, current_week)).fetchone()
    if row is None:
        return None
    flags = json.loads(row[2]).get("flags", {})
    return engine.MarketState(
        state=row[0], age_weeks=row[1],
        party_full=flags.get("party_full", False),
        party_empty=flags.get("party_empty", False),
        dead_weeks=flags.get("dead_weeks", 0))


def _persist(conn, market: str, week: str, new, scores: dict,
             as_of_str: str, prev) -> None:
    existing = conn.execute(
        "SELECT state FROM states WHERE market_id = ? AND week = ?",
        (market, week)).fetchone()
    conn.execute(
        "INSERT OR REPLACE INTO states VALUES (?, ?, ?, ?, ?)",
        (market, week, new.state, new.age_weeks, json.dumps(scores)))

    changed_vs_prev = (prev.state if prev else "NEUTRAL") != new.state
    already_journaled = existing is not None and existing[0] == new.state
    if changed_vs_prev and not already_journaled:
        price = conn.execute(
            "SELECT value FROM observations WHERE series_id = ? AND"
            " pub_date <= ? ORDER BY data_date DESC LIMIT 1",
            (drivers.MARKETS[market]["price"], as_of_str)).fetchone()
        conn.execute(
            "INSERT INTO journal (date, market_id, event_type, detail,"
            " price_at_event) VALUES (?, ?, 'state_change', ?, ?)",
            (as_of_str, market,
             f"{prev.state if prev else 'NEUTRAL'} -> {new.state}"
             f" | {json.dumps(scores)}",
             price[0] if price else None))
