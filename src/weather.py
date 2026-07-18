"""Weather light — F11, the market-wide overlay.

@context  Four gauges → GREEN/YELLOW/RED (product doc §12 Fix 6 v2.1). The
          light scales NEW position sizes globally (F12 w); it never picks
          investments and never forces exits. Gauge 2 uses the ROLLING 20-YR
          percentile (standing rule for drifting series).
@done     evaluate() — pure points logic: red = [cash<4] + [valuation>90] +
          [spread>80] + [liquidity falling]; 0=GREEN 1=YELLOW ≥2=RED; a
          missing gauge (None) contributes 0 points and is reported missing.
          light() — assembles the readouts from the db as-of.
@todo     Phase 6: divergence banner pairing (3.4 adds whale cash percentile).
@limits   evaluate is PURE; light reads only pub_date <= as_of.
@affects  states.run_week sizing via weekly_run; the report's first line.
"""

import sqlite3

from src import formulas, spine

THRESHOLDS = {"manager_cash": 4.0, "valuation_pct": 90.0, "spread_pct": 80.0}


def evaluate(readouts: dict) -> dict:
    """readouts: manager_cash, valuation_pct, spread_pct (floats or None),
    liquidity_falling (bool or None)."""
    gauges = {
        "manager_cash": _gauge(readouts.get("manager_cash"),
                               lambda v: v < THRESHOLDS["manager_cash"]),
        "valuation_pct": _gauge(readouts.get("valuation_pct"),
                                lambda v: v > THRESHOLDS["valuation_pct"]),
        "spread_pct": _gauge(readouts.get("spread_pct"),
                             lambda v: v > THRESHOLDS["spread_pct"]),
        "liquidity_falling": _gauge(readouts.get("liquidity_falling"),
                                    lambda v: v is True),
    }
    points = sum(1 for g in gauges.values() if g["red"] is True)
    light = "GREEN" if points == 0 else ("YELLOW" if points == 1 else "RED")
    return {"light": light, "points": points, "gauges": gauges}


def _gauge(value, is_red):
    return {"value": value, "red": None if value is None else bool(is_red(value))}


def light(conn: sqlite3.Connection, as_of: str) -> dict:
    cash_row = conn.execute(
        "SELECT value FROM observations WHERE series_id = 'manager_cash'"
        " AND pub_date <= ? ORDER BY data_date DESC LIMIT 1",
        (as_of,)).fetchone()
    liq = spine._values(conn, "net_liquidity", as_of)
    readouts = {
        "manager_cash": cash_row[0] if cash_row else None,
        "valuation_pct": formulas.pct_rank(
            spine._values(conn, "market_valuation", as_of),
            spine.WINDOW_OBS[("rolling20y", "weekly")]),
        "spread_pct": formulas.pct_rank(
            spine._values(conn, "fred_baa10y", as_of),
            spine.WINDOW_OBS[("rolling10y", "daily")]),
        "liquidity_falling": formulas.is_falling(liq, lag=13),
    }
    return evaluate(readouts)
