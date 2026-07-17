"""Historical replay — week-by-week re-run over as-of data.

@context  The Phase 2 acceptance harness (build plan): iterate Saturdays over
          stored history, computing states with ONLY data published on or
          before each week (the as-of filters inside states/spine/drivers do
          this for free). Also the seed of the Phase 5 falsification engine.
@done     run(): weekly loop start->end through states.run_week; analyze():
          per-market flip counts + condensed state timeline from the states
          table.
@todo     Phase 5: extend to 15 years, compute the four §16 criteria.
@limits   Replay writes the same states/journal tables as live runs
          (idempotent; change-journal guard prevents duplicates). Wall-clock
          ~1-2 min for 3 years x 5 markets.
@affects  states table, journal; reads everything the weekly run reads.
"""

import datetime as dt
import sqlite3

from src import drivers, states


def run(conn: sqlite3.Connection, start: dt.date, end: dt.date) -> dict:
    """Replay every week from start to end (inclusive), then analyze."""
    day = start
    while day <= end:
        states.run_week(conn, day)
        day += dt.timedelta(weeks=1)
    return analyze(conn, states.iso_week(start), states.iso_week(end))


def analyze(conn: sqlite3.Connection, first_week: str,
            last_week: str) -> dict:
    """{market: {flips, weeks, timeline: [(state, from, to), ...]}}"""
    out = {}
    for market in drivers.MARKETS:
        rows = conn.execute(
            "SELECT week, state FROM states WHERE market_id = ? AND"
            " week >= ? AND week <= ? ORDER BY week",
            (market, first_week, last_week)).fetchall()
        timeline, flips = [], 0
        for week, state in rows:
            if timeline and timeline[-1][0] == state:
                timeline[-1] = (state, timeline[-1][1], week)
            else:
                if timeline:
                    flips += 1
                timeline.append((state, week, week))
        out[market] = {"flips": flips, "weeks": len(rows),
                       "timeline": timeline}
    return out
