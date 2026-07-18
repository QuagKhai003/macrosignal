"""Spine derivations + weekly readout (Phase 1 — numbers, not states).

@context  Turns raw spine observations into the derived series and readouts
          the Phase 2 state machine will consume: net liquidity (F5),
          percentiles per registry windows (F1/F8), momentum flags (F4), the
          gold/real-yields driver correlation (F3). As-of discipline: every
          query filters pub_date <= as_of.
@done     derive_net_liquidity (unit-normalized to $bn: WALCL and WTREGEN are
          FRED-millions, RRPONTSYD billions — verified live 2026-07-18;
          component alignment = latest value at-or-before each WALCL date),
          summarize() readouts, weekly last-obs resampling for F3.
@todo     Phase 2: feed these readouts into the state machine instead of
          printing them.
@limits   No network. Readouts return None where history is insufficient
          (F1's contribute-nothing rule). Windows resolve via WINDOW_OBS from
          the registry window string + schedule — never hardcoded per call.
@affects  weekly_run.py; consumes src/formulas.py; reads/writes signals.db
          (writes only the derived net_liquidity series).
"""

import datetime as dt
import sqlite3
from bisect import bisect_right

from src import formulas

# registry window string + schedule -> observation count
# (52 weekly obs/yr; 252 trading days/yr)
WINDOW_OBS = {
    ("rolling3y", "weekly"): 156,
    ("rolling10y", "weekly"): 520,
    ("rolling10y", "daily"): 2520,
    ("rolling20y", "weekly"): 1040,
    ("rolling20y", "daily"): 5040,
}


def derive_net_liquidity(conn: sqlite3.Connection, as_of: str) -> int:
    """F5 in $bn: WALCL/1000 - WTREGEN/1000 - RRPONTSYD, one row per WALCL
    date, components = latest published value at or before that date."""
    walcl = _series_rows(conn, "fred_walcl", as_of)
    tga = _series_rows(conn, "fred_wtregen", as_of)
    rrp = _series_rows(conn, "fred_rrpontsyd", as_of)
    tga_dates = [r[0] for r in tga]
    rrp_dates = [r[0] for r in rrp]
    added = 0
    for data_date, pub, value in walcl:
        i = bisect_right(tga_dates, data_date) - 1
        j = bisect_right(rrp_dates, data_date) - 1
        if i < 0 or j < 0:
            continue
        level = formulas.net_liquidity(value / 1000.0, tga[i][2] / 1000.0,
                                       rrp[j][2])
        cur = conn.execute(
            "INSERT OR IGNORE INTO observations VALUES"
            " ('net_liquidity', ?, ?, ?)",
            (data_date, max(pub, tga[i][1], rrp[j][1]), level))
        added += cur.rowcount
    conn.commit()
    return added


def derive_market_valuation(conn: sqlite3.Connection, as_of: str) -> int:
    """Weather gauge 2 numerator over denominator: weekly last Wilshire close
    divided by the latest PUBLISHED GDP (forward-fill by pub_date — the as-of
    answer to quarterly lag). Stored as the derived market_valuation series."""
    wilshire = _series_rows(conn, "price_wilshire", as_of)
    gdp = _series_rows(conn, "fred_gdp", as_of)
    if not wilshire or not gdp:
        return 0
    weekly_last = {}
    for data_date, pub, value in wilshire:  # last close per ISO week wins
        iso = dt.date.fromisoformat(data_date).isocalendar()
        weekly_last[(iso.year, iso.week)] = (data_date, pub, value)
    gdp_pubs = [r[1] for r in gdp]
    added = 0
    for data_date, pub, close in weekly_last.values():
        i = bisect_right(gdp_pubs, data_date) - 1  # latest GDP published by then
        if i < 0:
            continue
        ratio = close / gdp[i][2]
        cur = conn.execute(
            "INSERT OR IGNORE INTO observations VALUES"
            " ('market_valuation', ?, ?, ?)",
            (data_date, max(pub, gdp[i][1]), ratio))
        added += cur.rowcount
    conn.commit()
    return added


def derive_rate_differential(conn: sqlite3.Connection, as_of: str) -> int:
    """The euro engine's driver: US 2-yr yield minus the ECB deposit rate
    (DGS2 - ECBDFR), one row per DGS2 date, ECBDFR = latest published at or
    before that date. Stored as the derived us_ez_rate_diff series."""
    us = _series_rows(conn, "fred_dgs2", as_of)
    ez = _series_rows(conn, "fred_ecbdfr", as_of)
    if not us or not ez:
        return 0
    ez_dates = [r[0] for r in ez]
    added = 0
    for data_date, pub, value in us:
        i = bisect_right(ez_dates, data_date) - 1
        if i < 0:
            continue
        diff = value - ez[i][2]
        cur = conn.execute(
            "INSERT OR IGNORE INTO observations VALUES"
            " ('us_ez_rate_diff', ?, ?, ?)",
            (data_date, max(pub, ez[i][1]), diff))
        added += cur.rowcount
    conn.commit()
    return added


def derive_corn_stocks_use(conn: sqlite3.Connection, as_of: str) -> int:
    """Corn's REAL driver (research R1, spec §3.2): stocks-to-use ratio.
    Implied use over the trailing year = stocks_{t-4q} + production credited
    between then and now − stocks_t (trade ignored — a stable small term).
    Production is credited at its harvest-year Dec 1 data_date. Ratio =
    stocks_t ÷ implied use; stored as corn_stocks_use (quarterly)."""
    stocks = _series_rows(conn, "corn_stocks", as_of)
    production = _series_rows(conn, "corn_production", as_of)
    if len(stocks) < 5 or not production:
        return 0
    prod_dates = [r[0] for r in production]
    added = 0
    for i in range(4, len(stocks)):
        d_now, pub_now, s_now = stocks[i]
        d_prev, pub_prev, s_prev = stocks[i - 4]
        # production credited in the window (d_prev, d_now]
        lo = bisect_right(prod_dates, d_prev)
        hi = bisect_right(prod_dates, d_now)
        prod_in_window = sum(production[j][2] for j in range(lo, hi))
        prod_pubs = [production[j][1] for j in range(lo, hi)]
        use = s_prev + prod_in_window - s_now
        if use <= 0:
            continue  # nonsense window (data gap); skip honestly
        ratio = s_now / use
        pub = max([pub_now, pub_prev] + prod_pubs)
        cur = conn.execute(
            "INSERT OR IGNORE INTO observations VALUES"
            " ('corn_stocks_use', ?, ?, ?)", (d_now, pub, ratio))
        added += cur.rowcount
    conn.commit()
    return added


def summarize(conn: sqlite3.Connection, as_of: str) -> dict:
    """The Phase 1 readouts, one dict — every value traceable to a formula."""
    out = {}
    for sid in ("cot_gold", "cot_wti", "cot_ust10y", "cot_eur", "cot_corn"):
        out[f"{sid}_party_pct"] = formulas.pct_rank(
            _values(conn, sid, as_of), WINDOW_OBS[("rolling3y", "weekly")])
    liq = _values(conn, "net_liquidity", as_of)
    out["net_liquidity_pct"] = formulas.pct_rank(
        liq, WINDOW_OBS[("rolling10y", "weekly")])
    out["net_liquidity_falling"] = formulas.is_falling(liq, lag=13)
    out["real_yield_pct"] = formulas.pct_rank(
        _values(conn, "fred_dfii10", as_of), WINDOW_OBS[("rolling10y", "daily")])
    out["market_valuation_pct"] = formulas.pct_rank(
        _values(conn, "market_valuation", as_of),
        WINDOW_OBS[("rolling20y", "weekly")])
    # gauge series = BAA10Y (L-001 fix: full history; HY OAS still capped)
    out["credit_spread_pct"] = formulas.pct_rank(
        _values(conn, "fred_baa10y", as_of),
        WINDOW_OBS[("rolling10y", "daily")])
    out["us_dollar_pct"] = formulas.pct_rank(
        _values(conn, "fred_dtwexbgs", as_of),
        WINDOW_OBS[("rolling10y", "daily")])
    for sid in ("price_gold", "price_wti", "price_ust10y", "price_eur",
                "price_corn"):
        out[f"{sid}_momentum"] = formulas.sma200_flag(_values(conn, sid, as_of))
    out["gold_realyield_corr_52w"] = _weekly_corr(
        conn, "fred_dfii10", "price_gold", as_of)
    return out


def _series_rows(conn, sid, as_of):
    return conn.execute(
        "SELECT data_date, pub_date, value FROM observations"
        " WHERE series_id = ? AND pub_date <= ? ORDER BY data_date",
        (sid, as_of)).fetchall()


def _values(conn, sid, as_of):
    return [r[2] for r in _series_rows(conn, sid, as_of)]


def _weekly_corr(conn, driver_sid, price_sid, as_of, window=52):
    """F3 on weekly frequency: last observation per ISO week, weeks present
    in BOTH series, correlation of the changes."""
    driver = _weekly_last(_series_rows(conn, driver_sid, as_of))
    price = _weekly_last(_series_rows(conn, price_sid, as_of))
    common = sorted(driver.keys() & price.keys())
    return formulas.corr_of_changes([driver[w] for w in common],
                                    [price[w] for w in common], window)


def _weekly_last(rows) -> dict:
    out = {}
    for data_date, _pub, value in rows:  # ordered by date -> last obs wins
        iso = dt.date.fromisoformat(data_date).isocalendar()
        out[(iso.year, iso.week)] = value
    return out
