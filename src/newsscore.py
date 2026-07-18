"""News scoring — F6 volume ratio + F7 greed ratio. Deterministic.

@context  Turns stored volumes and audit-logged labels into the fourth
          weekly question. The LLM is upstream and caged; everything here is
          arithmetic (Golden Rule). Thresholds are the spec's: loud >3.0
          (un-loud <2.0, hysteresis), quiet <1.5; greedy >0.6, scared <0.4;
          ≥30 labeled headlines or the signal reports insufficient.
@done     score(): F6 V = last-4-completed-weeks count ÷ mean 4-week count
          over the trailing 52 weeks (insufficient below 0.8×52 weeks of
          volume history); loud flag with hysteresis carry; F7 greed ratio
          over the trailing week's labeled headlines; news_state mapping for
          the engine (quiet / loud_greedy / loud_scared / neutral /
          insufficient).
@todo     —
@limits   PURE reads (pub_date/seen_date <= as_of). 'error' labels count
          toward nothing. The scared-and-abandoned flag needs the party score
          and is assembled in states.run_week.
@affects  states.run_week (news input + flag), the report.
"""

import datetime as dt
import sqlite3

from src import spine

LOUD_ENTER, LOUD_EXIT, QUIET_BELOW = 3.0, 2.0, 1.5
MIN_HEADLINES = 30
GREEDY_ABOVE, SCARED_BELOW = 0.6, 0.4
VOLUME_WEEKS = 52


def score(conn: sqlite3.Connection, theme: str, as_of: str,
          prev_loud: bool | None = None) -> dict:
    volume_ratio = _volume_ratio(conn, theme, as_of)
    loud = _loudness(volume_ratio, prev_loud)
    greed_ratio, n_labeled = _greed(conn, theme, as_of)

    if greed_ratio is None:
        direction = "insufficient"
    elif greed_ratio > GREEDY_ABOVE:
        direction = "greedy"
    elif greed_ratio < SCARED_BELOW:
        direction = "scared"
    else:
        direction = "neutral"

    if volume_ratio is None:
        news_state = "insufficient"
    elif loud and direction == "greedy":
        news_state = "loud_greedy"
    elif loud and direction == "scared":
        news_state = "loud_scared"
    elif volume_ratio < QUIET_BELOW:
        news_state = "quiet"
    else:
        news_state = "neutral"

    return {"volume_ratio": volume_ratio, "loud": loud,
            "greed_ratio": greed_ratio, "n_labeled": n_labeled,
            "direction": direction, "news_state": news_state}


def _volume_ratio(conn, theme, as_of) -> float | None:
    """F6 on completed weeks: sum(last 4) / mean of rolling 4-week sums
    across the trailing 52 weeks."""
    counts = spine._values(conn, f"news_vol_{theme}", as_of)[-VOLUME_WEEKS:]
    if len(counts) < 0.8 * VOLUME_WEEKS:
        return None
    rolling = [sum(counts[i:i + 4]) for i in range(len(counts) - 3)]
    mean = sum(rolling) / len(rolling)
    if mean == 0:
        return None
    return sum(counts[-4:]) / mean


def _loudness(volume_ratio, prev_loud) -> bool | None:
    if volume_ratio is None:
        return None
    if volume_ratio > LOUD_ENTER:
        return True
    if volume_ratio < LOUD_EXIT:
        return False
    return bool(prev_loud)  # hysteresis band [2.0, 3.0]


def _greed(conn, theme, as_of) -> tuple[float | None, int]:
    """F7 over the trailing 7 days' labeled headlines ('error' excluded)."""
    since = (dt.date.fromisoformat(as_of) - dt.timedelta(days=7)).isoformat()
    rows = conn.execute(
        "SELECT label, COUNT(*) FROM headlines WHERE theme = ? AND"
        " seen_date > ? AND seen_date <= ? AND label IN"
        " ('excited', 'scared', 'neutral') GROUP BY label",
        (theme, since, as_of)).fetchall()
    counts = dict(rows)
    n = sum(counts.values())
    if n < MIN_HEADLINES:
        return None, n
    excited, scared = counts.get("excited", 0), counts.get("scared", 0)
    if excited + scared == 0:
        return 0.5, n  # all-neutral week: dead center, no flag either way
    return excited / (excited + scared), n
