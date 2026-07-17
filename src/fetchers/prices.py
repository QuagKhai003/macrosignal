"""Prices fetcher — daily closes via the Yahoo Finance chart API.

@context  Feeds the momentum gate (F4) and, later, the replay engine. Source:
          Yahoo v8 chart JSON (stooq, the spec's other learning-grade option,
          sits behind a JavaScript challenge — decision 2026-07-18). Symbols
          are config in signals.yaml (`markets:` map). Learning-grade data by
          design; upgrade path is Tiingo (product doc §6).
@done     fetch(): per market, full available daily history (range=max),
          stored under price_<market>; bar date derived from the exchange's
          gmtoffset; pub_date = data_date (lag 0); idempotent; loud failures.
@todo     Nothing scheduled; Tiingo swap lands behind this same seam if Yahoo
          degrades.
@limits   Unofficial API — the shape guards raise FetchError on any drift.
          Null closes (holidays) are skipped, never zero-filled.
@affects  weekly_run (1.5); observations under price_gold, price_wti,
          price_ust10y, price_eur, price_corn; F4 momentum; Phase 5 replay.
"""

import datetime as dt
import sqlite3

import requests

from src.fetchers import base

API_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; macrosignal research)"}


class FetchError(RuntimeError):
    pass


def fetch(entry: dict, conn: sqlite3.Connection, session=None) -> int:
    session = session or requests.Session()
    added = 0
    for series_id, symbol in entry["markets"].items():
        rows = _rows(session, symbol)  # fetch BEFORE registering the series
        base.ensure_series_row(conn, series_id, entry,
                               f"Yahoo {symbol} (component of {entry['series_id']})")
        added += base.insert_observations(conn, series_id, rows)
    conn.commit()
    return added


def _rows(session, symbol: str) -> list[tuple[str, str, float]]:
    # explicit epoch bounds: "range=max" silently falls back to ~1 year
    resp = session.get(API_URL.format(symbol=symbol),
                       params={"period1": "0", "period2": "9999999999",
                               "interval": "1d"},
                       headers=_HEADERS, timeout=120)
    if resp.status_code != 200:
        raise FetchError(f"prices {symbol}: HTTP {resp.status_code}")
    try:
        result = resp.json()["chart"]["result"][0]
        offset = int(result["meta"].get("gmtoffset", 0))
        timestamps = result["timestamp"]
        closes = result["indicators"]["quote"][0]["close"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise FetchError(f"prices {symbol}: unexpected payload") from exc
    rows = []
    for ts, close in zip(timestamps, closes):
        if close is None:
            continue  # holiday/missing bar — never zero-fill
        day = dt.datetime.fromtimestamp(ts + offset, tz=dt.UTC).date().isoformat()
        rows.append((day, day, float(close)))
    if not rows:
        raise FetchError(f"prices {symbol}: zero rows returned")
    return rows
