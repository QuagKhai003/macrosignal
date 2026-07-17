"""Driver plugins — per-market engine answers (question 1 of the four).

@context  Each market's "reason to own" is a plugin returning engine ✓/✗/None
          plus driver-alive status (§3.2, F3, F9 commodity variants). Phase 2
          scope (build plan): gold + oil only; ust10y/eur/corn return None —
          honestly "no driver defined", never guessed.
@done     gold: real yields falling (13-week weekly change < 0) AND driver
          alive (52-wk rolling corr sign matches the 10-yr historical sign,
          |rho| >= 0.1). oil: commercial crude stocks below the same-calendar-
          week 5-yr average (>= 4 of 5 prior years present). MARKETS registry.
@todo     Phase 2.2 consumes alive-streaks for the 26-week dead rule; more
          plugins as markets are admitted (Phase 6 expansion).
@limits   Read-only on the db; as-of filtered (pub_date <= as_of). None means
          "cannot answer", and callers must treat it as not-✓.
@affects  src/engine.py (2.2), weekly replay (2.5); reads observations written
          by src/fetchers/*.
"""

import datetime as dt
import sqlite3

from src import spine

# market id -> its component series (the state machine's working universe)
MARKETS = {
    "gold": {"cot": "cot_gold", "price": "price_gold"},
    "wti": {"cot": "cot_wti", "price": "price_wti"},
    "ust10y": {"cot": "cot_ust10y", "price": "price_ust10y"},
    "eur": {"cot": "cot_eur", "price": "price_eur"},
    "corn": {"cot": "cot_corn", "price": "price_corn"},
}


def engines(conn: sqlite3.Connection, as_of: str) -> dict:
    """{market: {"engine": True/False/None, "alive": True/False/None}}"""
    out = {m: {"engine": None, "alive": None} for m in MARKETS}
    out["gold"] = gold_engine(conn, as_of)
    out["wti"] = oil_engine(conn, as_of)
    return out


def gold_engine(conn: sqlite3.Connection, as_of: str) -> dict:
    """Engine ✓ = real yields FALLING (13-wk change < 0) AND driver alive.
    Alive = sign(rho_52wk) == sign(rho_10yr historical) and |rho| >= 0.1."""
    weekly = spine._weekly_last(spine._series_rows(conn, "fred_dfii10", as_of))
    values = [weekly[w] for w in sorted(weekly)]
    falling = values[-1] < values[-14] if len(values) >= 14 else None

    rho_now = spine._weekly_corr(conn, "fred_dfii10", "price_gold", as_of,
                                 window=52)
    rho_hist = spine._weekly_corr(conn, "fred_dfii10", "price_gold", as_of,
                                  window=520)
    if rho_now is None or rho_hist is None:
        alive = None
    else:
        alive = (rho_now * rho_hist > 0) and abs(rho_now) >= 0.1

    if falling is None or alive is None:
        return {"engine": None, "alive": alive}
    return {"engine": bool(falling and alive), "alive": alive}


def oil_engine(conn: sqlite3.Connection, as_of: str) -> dict:
    """Engine ✓ = commercial crude stocks below their same-calendar-week
    average of the prior 5 years (tightness). Needs >= 4 of 5 prior years
    within +/-10 days of the anniversary date. Inventory drivers have no
    correlation-alive concept in v1 -> alive mirrors data presence."""
    rows = spine._series_rows(conn, "oil_inventories", as_of)
    if not rows:
        return {"engine": None, "alive": None}
    latest_date, _, latest_value = rows[-1]
    anchor = dt.date.fromisoformat(latest_date)

    priors = []
    for years_back in range(1, 6):
        target = anchor - dt.timedelta(days=round(365.25 * years_back))
        nearest = min(rows, key=lambda r: abs(
            (dt.date.fromisoformat(r[0]) - target).days))
        if abs((dt.date.fromisoformat(nearest[0]) - target).days) <= 10:
            priors.append(nearest[2])
    if len(priors) < 4:
        return {"engine": None, "alive": None}
    return {"engine": bool(latest_value < sum(priors) / len(priors)),
            "alive": True}
