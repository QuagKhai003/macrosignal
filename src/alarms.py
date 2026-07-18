"""Anti-noise — the alarm budget (build plan Phase 6 task 2). Pure.

@context  A machine that fires too often is as useless as one that never
          speaks (§16 muteness has a loud twin: over-alarming). This counts
          actionable events over the trailing rolling year and raises a
          self-distrust banner past the budget, so the operator knows to trust
          the machine LESS, not more, in a noisy stretch.
@done     alarm_budget(): counts transitions INTO CONFIRMED/CROWDED/BROKEN
          (same ACTIONABLE set the falsifier grades) across the last 52 ISO
          weeks from the states table; banner when over BUDGET. veto_stats():
          rolling veto tally from the journal (the obeyed-the-machine ledger).
@todo     Quarterly-review helpers (build plan task 5) when that ritual lands.
@limits   PURE reads. BUDGET is the spec's number (>20/yr = distrust), not
          tuned. Rolling window = 52 ISO weeks ending at as_of.
@affects  the weekly report (banner line); reads states + journal.
"""

import datetime as dt
import sqlite3

ACTIONABLE = {"CONFIRMED", "CROWDED", "BROKEN"}
BUDGET = 20  # >20 actionable alarms / rolling year -> self-distrust (§16)


def alarm_budget(conn: sqlite3.Connection, as_of: dt.date) -> dict:
    """Actionable events in the trailing 52 ISO weeks, vs the budget."""
    cutoff = _iso_week(as_of - dt.timedelta(weeks=52))
    now = _iso_week(as_of)
    events = 0
    markets = [r[0] for r in conn.execute(
        "SELECT DISTINCT market_id FROM states")]
    for market in markets:
        rows = conn.execute(
            "SELECT week, state FROM states WHERE market_id = ?"
            " ORDER BY week", (market,)).fetchall()
        prev = None
        for week, state in rows:
            if (prev is not None and state != prev and state in ACTIONABLE
                    and cutoff < week <= now):
                events += 1
            prev = state
    over = events > BUDGET
    return {"events": events, "budget": BUDGET, "over_budget": over,
            "banner": ("Too many alarms this year — trust the machine LESS,"
                       " not more." if over else None)}


def veto_stats(conn: sqlite3.Connection, as_of: dt.date) -> dict:
    """Rolling-year veto count from the journal (event_type='veto')."""
    cutoff = (as_of - dt.timedelta(weeks=52)).isoformat()
    n = conn.execute(
        "SELECT COUNT(*) FROM journal WHERE event_type = 'veto'"
        " AND date > ?", (cutoff,)).fetchone()[0]
    return {"vetoes_rolling_year": n}


def _iso_week(day: dt.date) -> str:
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"
