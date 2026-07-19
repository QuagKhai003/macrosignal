"""Forward base rates — what history says came next (the honest forecast).

@context  The user's vision: the app forecasts the world, not price targets.
          This module is the "beyond" half: for each market and state, the
          distribution of ACTUAL forward returns across every historical
          week that showed the same state (replay states + weekly prices).
          Base rates with their spread and worst case — statistics, never
          prophecy; a state's forecast is the honest record of weeks like it.
@done     base_rates(): per market/state over HORIZON_WEEKS — count, mean,
          25th/75th percentile, worst; None-safe (missing prices skipped);
          MIN_SAMPLE gate (fewer alike-weeks than 30 → no number, honest).
          sentence(): the plain-language rendering used by the report.
@todo     Dashboard panel (market page) — next batch.
@limits   Pure db reads over the states table the replay wrote. Base rates
          describe the past; they are the app's only honest way to speak
          about the future (pre-registered stance, §16 spirit).
@affects  src/report.py (forward lines); tests/test_forward.py.
"""

import sqlite3

from src import drivers, falsify

HORIZON_WEEKS = 13  # one quarter ahead — the app's natural rhythm
MIN_SAMPLE = 30     # fewer alike-weeks than this -> honestly no number


def base_rates(conn: sqlite3.Connection, as_of: str,
               horizon: int = HORIZON_WEEKS) -> dict:
    """{market: {state: {n, mean, p25, p75, worst}}} — forward percent
    returns over `horizon` weeks from every historical week in that state."""
    states = falsify.load_states(conn)
    prices = falsify.load_weekly_prices(conn, as_of)
    out = {}
    for market in drivers.MARKETS:
        weeks = sorted(prices.get(market, {}))
        index = {w: i for i, w in enumerate(weeks)}
        per_state: dict[str, list[float]] = {}
        for week, state in states.get(market, {}).items():
            i = index.get(week)
            if i is None or i + horizon >= len(weeks):
                continue
            start, end = prices[market][weeks[i]], \
                prices[market][weeks[i + horizon]]
            if start:
                per_state.setdefault(state, []).append(
                    100.0 * (end / start - 1.0))
        out[market] = {state: _stats(returns)
                       for state, returns in per_state.items()}
    return out


def _stats(returns: list[float]) -> dict | None:
    if len(returns) < MIN_SAMPLE:
        return None
    ordered = sorted(returns)
    n = len(ordered)
    return {"n": n,
            "mean": sum(ordered) / n,
            "p25": ordered[n // 4],
            "p75": ordered[(3 * n) // 4],
            "worst": ordered[0]}


def sentence(stats: dict | None, horizon: int = HORIZON_WEEKS) -> str:
    """One plain line per the no-jargon rule; honest when silent."""
    months = round(horizon / 4.33)
    if stats is None:
        return (f"Not enough alike-weeks in the record to say what the next"
                f" {months} months usually looked like.")
    return (f"From weeks like this one, the next {months} months averaged"
            f" {stats['mean']:+.1f}%; half the time the result landed between"
            f" {stats['p25']:+.1f}% and {stats['p75']:+.1f}%; the worst seen"
            f" was {stats['worst']:+.1f}%. ({stats['n']} alike-weeks)")
