"""Live WTI curve continuation — Yahoo CME contract months (research R3b).

@context  The R3-kept curve leg (F9: oil ✓ = ... OR backwardated) lost its
          feed when EIA stopped updating RCLC1/RCLC4 in 2024-04. This module
          continues the SAME admitted signal live: each weekly run stores one
          close for the current front-month contract (yh_clc1) and the
          contract three delivery months later (yh_clc4) from the Yahoo chart
          API. src/spine merges both sources into oil_curve_spread (EIA
          authoritative for its span). Called by src/fetchers/eia.fetch when
          the entry carries `live_curve`; no separate registry entry — the
          oil_curve signal was admitted on its 40-yr EIA history.
@done     Contract symbol arithmetic (delivery-month codes F..Z, year
          rollover, roll to next delivery on/after the 15th — safely before
          the ~20th expiry); latest-close-only storage (no roll ambiguity:
          contract ranks are only certain for the fetch date); idempotent;
          loud failures.
@todo     —
@limits   One observation per run (weekly cadence) — the 2024-04→2026-07 gap
          stays empty (expired Yahoo contracts 404; honest None in replay).
          Learning-grade delayed prices vs EIA settlements: fine for a
          spread-positive test.
@affects  observations under yh_clc1/yh_clc4; src/spine.
          derive_oil_curve_spread; src/drivers.oil_curve_backwardated.
"""

import datetime as dt
import sqlite3

import requests

from src.fetchers import base

API_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; macrosignal research)"}
_MONTH_CODES = "FGHJKMNQUVXZ"  # CME delivery-month letters, Jan..Dec


class FetchError(RuntimeError):
    pass


def fetch_continuation(entry: dict, conn: sqlite3.Connection, session=None,
                       today: dt.date | None = None) -> int:
    session = session or requests.Session()
    today = today or dt.date.today()
    root = entry["live_curve"]["symbol_root"]
    added = 0
    for sid, rank_offset in (("yh_clc1", 0), ("yh_clc4", 3)):
        symbol = contract_symbol(root, today, rank_offset)
        day, close = _latest_close(session, symbol)
        base.ensure_series_row(conn, sid, entry,
                               f"Yahoo {symbol} live curve continuation")
        added += base.insert_observations(conn, sid, [(day, day, close)])
    conn.commit()
    return added


def contract_symbol(root: str, on: dt.date, rank_offset: int) -> str:
    """CME symbol for the front contract (rank_offset 0) or a later delivery
    month, as of `on`. Front delivery month = next calendar month, rolled one
    further on/after the 15th (WTI trading stops ~the 20th of the month
    before delivery — rolling early always names an active contract)."""
    months_ahead = 1 + (on.day >= 15) + rank_offset
    index = on.year * 12 + (on.month - 1) + months_ahead
    year, month = divmod(index, 12)
    return f"{root}{_MONTH_CODES[month]}{year % 100:02d}.NYM"


def _latest_close(session, symbol: str) -> tuple[str, float]:
    resp = session.get(API_URL.format(symbol=symbol),
                       params={"range": "5d", "interval": "1d"},
                       headers=_HEADERS, timeout=120)
    if resp.status_code != 200:
        raise FetchError(f"curve {symbol}: HTTP {resp.status_code}")
    try:
        result = resp.json()["chart"]["result"][0]
        offset = int(result["meta"].get("gmtoffset", 0))
        pairs = [(ts, close) for ts, close in
                 zip(result["timestamp"],
                     result["indicators"]["quote"][0]["close"])
                 if close is not None]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise FetchError(f"curve {symbol}: unexpected payload") from exc
    if not pairs:
        raise FetchError(f"curve {symbol}: zero closes returned")
    ts, close = pairs[-1]
    day = dt.datetime.fromtimestamp(ts + offset, tz=dt.UTC).date().isoformat()
    return day, float(close)
