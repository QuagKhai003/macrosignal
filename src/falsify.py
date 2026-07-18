"""F14 falsification criteria — the pre-registered graders. Pure core.

@context  §16's four pass/fail questions, frozen BEFORE the replay so the
          machine can genuinely fail. Pure functions over plain dicts; thin
          db assemblers at the bottom. No thresholds may change here without
          a logged spec event.
@done     state_information (CONFIRMED weeks must out-return NEUTRAL weeks,
          next-week returns so no lookahead), crowd_test (fwd-26wk return
          after entering CROWDED ≤ after entering CONFIRMED), weather_overlap
          (≥6 of the 10 worst non-overlapping 26-week index windows touch
          YELLOW/RED), event_rate (entries into CONFIRMED/CROWDED/BROKEN,
          band [5,15]/yr, hard fail <3). Assemblers read states/observations.
@todo     5.3 wires these into the replay report (docs/VALIDATION.md).
@limits   PURE calculators; sides with no data return None (never a fake
          pass/fail). Actionable = transitions INTO {CONFIRMED, CROWDED,
          BROKEN} (buy / exit-bell / exit — each demands an action).
@affects  the Phase 5 verdict; reads states + observations via assemblers.
"""

import json
import sqlite3

from src import drivers, spine

WORST_WINDOWS = 10
WINDOW_WEEKS = 26
OVERLAP_PASS_MIN = 6
EVENT_BAND = (5.0, 15.0)
EVENT_HARD_FAIL = 3.0
ACTIONABLE = {"CONFIRMED", "CROWDED", "BROKEN"}


# ── criterion 1: states carry information ────────────────────────────────────

def state_information(states: dict, weekly_prices: dict) -> dict:
    """states: {market: {week: state}}; weekly_prices: {market: {week: px}}.
    Grades mean NEXT-week return during CONFIRMED vs NEUTRAL, per market."""
    out = {}
    for market, week_states in states.items():
        returns = _next_week_returns(weekly_prices.get(market, {}))
        buckets = {"CONFIRMED": [], "NEUTRAL": []}
        for week, state in week_states.items():
            if state in buckets and week in returns:
                buckets[state].append(returns[week])
        if not buckets["CONFIRMED"] or not buckets["NEUTRAL"]:
            out[market] = {"pass": None, "confirmed_weeks": len(buckets["CONFIRMED"]),
                           "neutral_weeks": len(buckets["NEUTRAL"])}
            continue
        c = sum(buckets["CONFIRMED"]) / len(buckets["CONFIRMED"])
        n = sum(buckets["NEUTRAL"]) / len(buckets["NEUTRAL"])
        out[market] = {"confirmed_mean": c, "neutral_mean": n, "pass": c > n,
                       "confirmed_weeks": len(buckets["CONFIRMED"]),
                       "neutral_weeks": len(buckets["NEUTRAL"])}
    return out


# ── criterion 2: the crowd meter works ───────────────────────────────────────

def crowd_test(states: dict, weekly_prices: dict) -> dict:
    """mean fwd-26wk return after ENTERING CROWDED must be <= after entering
    CONFIRMED. None where either entry set is empty (e.g. no historical news
    -> CROWDED unreachable in replay)."""
    fwd = {"CROWDED": [], "CONFIRMED": []}
    for market, week_states in states.items():
        prices = weekly_prices.get(market, {})
        ordered = sorted(week_states)
        for i, week in enumerate(ordered):
            if i == 0 or week_states[week] == week_states[ordered[i - 1]]:
                continue
            state = week_states[week]
            if state in fwd:
                r = _forward_return(prices, week, WINDOW_WEEKS)
                if r is not None:
                    fwd[state].append(r)
    if not fwd["CROWDED"] or not fwd["CONFIRMED"]:
        return {"pass": None, "crowded_entries": len(fwd["CROWDED"]),
                "confirmed_entries": len(fwd["CONFIRMED"])}
    crowded = sum(fwd["CROWDED"]) / len(fwd["CROWDED"])
    confirmed = sum(fwd["CONFIRMED"]) / len(fwd["CONFIRMED"])
    return {"crowded_fwd26": crowded, "confirmed_fwd26": confirmed,
            "pass": crowded <= confirmed,
            "crowded_entries": len(fwd["CROWDED"]),
            "confirmed_entries": len(fwd["CONFIRMED"])}


# ── criterion 3: the weather light works ─────────────────────────────────────

def weather_overlap(weather: dict, index_weekly: dict) -> dict:
    """weather: {week: GREEN/YELLOW/RED}; index_weekly: {week: px}. The 10
    worst NON-OVERLAPPING 26-week index windows WITHIN THE REPLAY PERIOD
    (§16: "the replay period's largest drawdowns" — the index is clipped to
    the weeks the weather light exists for) must touch YELLOW/RED in >=6
    cases."""
    if not weather:
        return {"windows": [], "hits": 0, "pass": None}
    lo, hi = min(weather), max(weather)
    index_weekly = {w: px for w, px in index_weekly.items() if lo <= w <= hi}
    weeks = sorted(index_weekly)
    windows = []
    for i in range(len(weeks) - WINDOW_WEEKS):
        start, end = weeks[i], weeks[i + WINDOW_WEEKS]
        r = index_weekly[end] / index_weekly[start] - 1.0
        windows.append((r, i, start, end))
    windows.sort()
    chosen, used = [], set()
    for r, i, start, end in windows:
        span = set(range(i, i + WINDOW_WEEKS + 1))
        if span & used:
            continue
        chosen.append((r, start, end))
        used |= span
        if len(chosen) == WORST_WINDOWS:
            break
    hits = 0
    for _r, start, end in chosen:
        span_weeks = [w for w in weeks if start <= w <= end]
        if any(weather.get(w) in ("YELLOW", "RED") for w in span_weeks):
            hits += 1
    return {"windows": [(round(r, 4), s, e) for r, s, e in chosen],
            "hits": hits, "pass": hits >= OVERLAP_PASS_MIN
            if len(chosen) == WORST_WINDOWS else None}


# ── criterion 4: muteness ────────────────────────────────────────────────────

def event_rate(states: dict, years: float) -> dict:
    """Actionable events per year across all markets: transitions INTO
    CONFIRMED/CROWDED/BROKEN. Band [5,15]; hard fail < 3."""
    events = 0
    for week_states in states.values():
        ordered = sorted(week_states)
        for i in range(1, len(ordered)):
            new = week_states[ordered[i]]
            if new != week_states[ordered[i - 1]] and new in ACTIONABLE:
                events += 1
    rate = events / years if years > 0 else 0.0
    verdict = ("hard_fail" if rate < EVENT_HARD_FAIL else
               "pass" if EVENT_BAND[0] <= rate <= EVENT_BAND[1] else
               "out_of_band")
    return {"events": events, "per_year": rate, "verdict": verdict}


# ── helpers + db assemblers ──────────────────────────────────────────────────

def _next_week_returns(weekly: dict) -> dict:
    weeks = sorted(weekly)
    return {weeks[i]: weekly[weeks[i + 1]] / weekly[weeks[i]] - 1.0
            for i in range(len(weeks) - 1) if weekly[weeks[i]]}


def _forward_return(weekly: dict, week, n: int):
    weeks = sorted(weekly)
    if week not in weekly:
        return None
    i = weeks.index(week)
    if i + n >= len(weeks):
        return None
    return weekly[weeks[i + n]] / weekly[week] - 1.0


def load_states(conn: sqlite3.Connection) -> dict:
    out = {m: {} for m in drivers.MARKETS}
    for market, week, state in conn.execute(
            "SELECT market_id, week, state FROM states"):
        out.setdefault(market, {})[week] = state
    return out


def load_weather(conn: sqlite3.Connection) -> dict:
    out = {}
    for week, sj in conn.execute(
            "SELECT week, scores_json FROM states WHERE market_id = 'gold'"):
        out[week] = json.loads(sj).get("weather")
    return out


def load_weekly_prices(conn: sqlite3.Connection, as_of: str) -> dict:
    out = {}
    for market, series in drivers.MARKETS.items():
        weekly = spine._weekly_last(
            spine._series_rows(conn, series["price"], as_of))
        out[market] = {f"{y}-W{w:02d}": v for (y, w), v in weekly.items()}
    return out


def load_index_weekly(conn: sqlite3.Connection, as_of: str) -> dict:
    weekly = spine._weekly_last(
        spine._series_rows(conn, "price_wilshire", as_of))
    return {f"{y}-W{w:02d}": v for (y, w), v in weekly.items()}
