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


# market -> (driver series, price series) for the "falling driver" engines.
# Each rallies when its driver FALLS, and the F3 alive-check confirms the
# historical sign still holds (whatever direction it is): gold vs real yields,
# bonds vs inflation expectations, the euro vs the US-EZ rate gap.
FALLING_DRIVERS = {
    "gold": ("fred_dfii10", "price_gold"),
    "ust10y": ("fred_t10yie", "price_ust10y"),
    "eur": ("us_ez_rate_diff", "price_eur"),
}


def engines(conn: sqlite3.Connection, as_of: str,
            prev: dict | None = None) -> dict:
    """{market: {"engine": True/False/None, "alive": True/False/None}}.
    prev = {market: last week's engine answer} for hysteresis carry."""
    prev = prev or {}
    out = {m: {"engine": None, "alive": None} for m in MARKETS}
    for market, (driver, price) in FALLING_DRIVERS.items():
        out[market] = falling_driver_engine(conn, driver, price, as_of)
    out["wti"] = oil_engine(conn, as_of, prev_engine=prev.get("wti"))
    return out


def falling_driver_engine(conn: sqlite3.Connection, driver_sid: str,
                          price_sid: str, as_of: str) -> dict:
    """Engine ✓ = the driver is FALLING (13-wk weekly change < 0) AND the
    driver is alive (sign of the 52-wk correlation of changes matches its
    10-yr historical sign, |rho| >= 0.1). The generalized gold engine."""
    weekly = spine._weekly_last(spine._series_rows(conn, driver_sid, as_of))
    values = [weekly[w] for w in sorted(weekly)]
    falling = values[-1] < values[-14] if len(values) >= 14 else None

    rho_now = spine._weekly_corr(conn, driver_sid, price_sid, as_of, window=52)
    rho_hist = spine._weekly_corr(conn, driver_sid, price_sid, as_of, window=520)
    if rho_now is None or rho_hist is None:
        alive = None
    else:
        alive = (rho_now * rho_hist > 0) and abs(rho_now) >= 0.1

    if falling is None or alive is None:
        return {"engine": None, "alive": alive}
    return {"engine": bool(falling and alive), "alive": alive}


def gold_engine(conn: sqlite3.Connection, as_of: str) -> dict:
    """Back-compat alias — gold is a falling-driver engine (real yields)."""
    return falling_driver_engine(conn, "fred_dfii10", "price_gold", as_of)


def oil_engine(conn: sqlite3.Connection, as_of: str,
               prev_engine: bool | None = None,
               exit_band: float = 0.02) -> dict:
    """Engine ✓ = commercial crude stocks below their same-calendar-week
    average of the prior 5 years (tightness). Needs >= 4 of 5 prior years
    within +/-10 days of the anniversary date. §5b two-trigger hysteresis
    (rule decision 2026-07-18): turns ✓ below the average, turns ✗ only above
    (1 + exit_band) x the average; in between the previous answer carries
    (fresh market in the band -> ✗). Inventory drivers have no correlation-
    alive concept in v1 -> alive mirrors data presence."""
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
    average = sum(priors) / len(priors)
    if latest_value < average:
        engine = True
    elif latest_value > (1.0 + exit_band) * average:
        engine = False
    else:
        engine = prev_engine if prev_engine is not None else False
    return {"engine": engine, "alive": True}
