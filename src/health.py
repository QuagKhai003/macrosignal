"""Health check — is the weekly habit alive? (batch 6.4, --health).

@context  The Saturday scheduled task needs a cheap self-check the operator
          (or the task itself) can run without fetching anything: did the
          last run happen recently, did its fetchers fail, is the alert
          budget sane, how fresh is the data.
@done     check(): last run row (recency vs MAX_RUN_AGE_DAYS + its own
          failure count), newest observation pub_date, rolling-year
          actionable events, exit 0 healthy / 1 stale-or-silent. Pure db
          reads; safe at any time.
@todo     —
@limits   No network, no writes. "Healthy" means the HABIT is alive — it
          says nothing about signal quality (VALIDATION.md owns that).
@affects  weekly_run.py --health; the scheduled task's failure visibility.
"""

import datetime as dt
import re
import sqlite3

MAX_RUN_AGE_DAYS = 8  # a weekly habit is stale after a missed Saturday


def check(conn: sqlite3.Connection, today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    run = conn.execute(
        "SELECT date, detail FROM journal WHERE event_type = 'run'"
        " ORDER BY journal_id DESC LIMIT 1").fetchone()
    if run is None:
        print("HEALTH: FAIL — no run has ever been journaled.")
        return 1
    run_date, detail = run
    age = (today - dt.date.fromisoformat(run_date)).days
    failures = _failures(detail)
    newest = conn.execute(
        "SELECT MAX(pub_date) FROM observations").fetchone()[0]
    events = conn.execute(
        "SELECT COUNT(*) FROM journal WHERE event_type IN"
        " ('state_change', 'flag') AND date >= ?",
        ((today - dt.timedelta(days=365)).isoformat(),)).fetchone()[0]

    stale = age > MAX_RUN_AGE_DAYS
    verdict = "FAIL" if stale else "OK"
    print(f"HEALTH: {verdict}")
    print(f"  last run: {run_date} ({age} days ago"
          f"{', STALE' if stale else ''}), {failures} fetch failures")
    print(f"  newest observation published: {newest}")
    print(f"  journal events, rolling year: {events}")
    return 1 if stale else 0


def _failures(detail: str) -> int:
    m = re.search(r"(\d+) fetch failures", detail)
    return int(m.group(1)) if m else 0
