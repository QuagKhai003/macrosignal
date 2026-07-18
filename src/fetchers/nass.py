"""USDA NASS fetcher — corn grain stocks (corn's engine input).

@context  Corn's "reason to own" (product doc §3.2: agriculture = stocks-to-
          use). Source: USDA NASS Quick Stats API (free key). We store the
          quarterly Grain Stocks series and compare each quarter to its own
          seasonal history in src/drivers.corn_engine (the oil pattern, but
          quarterly). Query params are config in signals.yaml `nass_query`.
@done     fetch(): pull corn STOCKS (BU, national, survey), parse year +
          reference_period_desc -> quarter data_date, pub_date = +pub_lag_days;
          skip withheld "(D)"/"(NA)" and comma-format the values; idempotent;
          store under corn_stocks; raise FetchError loud.
@todo     stocks-to-use RATIO (needs the USE series) if the raw-stocks
          seasonal proxy proves too coarse.
@limits   Requires USDA_NASS_API_KEY unless a session is injected (tests).
          Quarterly data; the engine keys on the reference quarter, not weeks.
@affects  weekly_run; observations under corn_stocks; src/drivers.corn_engine.
"""

import datetime as dt
import sqlite3

import requests

from src import config
from src.fetchers import base

API_URL = "https://quickstats.nass.usda.gov/api/api_GET/"
# "FIRST OF <MONTH>" reference period -> the quarter's stocks-as-of date
_PERIOD_MONTH = {"FIRST OF MAR": 3, "FIRST OF JUN": 6,
                 "FIRST OF SEP": 9, "FIRST OF DEC": 12}


class FetchError(RuntimeError):
    pass


def fetch(entry: dict, conn: sqlite3.Connection, session=None) -> int:
    if session is None:
        key = config.get_key("USDA_NASS_API_KEY")
        if not key:
            raise FetchError("USDA_NASS_API_KEY missing from environment/.env")
        session = _KeyedSession(requests.Session(), key)

    lag = dt.timedelta(days=int(entry["pub_lag_days"]))
    added = _fetch_stocks(entry, conn, session, lag)
    if entry.get("nass_production_query"):
        added += _fetch_production(entry, conn, session)
    conn.commit()
    return added


def _fetch_stocks(entry, conn, session, lag) -> int:
    records = _records(session, entry["nass_query"])
    rows = []
    for r in records:
        month = _PERIOD_MONTH.get((r.get("reference_period_desc") or "").upper())
        value = _num(r.get("Value"))
        year = r.get("year")
        if month is None or value is None or not year:
            continue
        data_date = dt.date(int(year), month, 1)
        pub = (data_date + lag).isoformat()
        rows.append((data_date.isoformat(), pub, value))
    if not rows:
        raise FetchError("NASS: zero usable corn-stocks rows")
    base.ensure_series_row(conn, "corn_stocks", entry, "USDA NASS corn grain stocks")
    return base.insert_observations(conn, "corn_stocks", rows)


def _fetch_production(entry, conn, session) -> int:
    """Annual production (the USE side of stocks-to-use, research R1).
    data_date = harvest-year Dec 1; pub = the following Jan 15 (the annual
    Crop Production report lands mid-January)."""
    records = _records(session, entry["nass_production_query"])
    rows = []
    for r in records:
        value = _num(r.get("Value"))
        year = r.get("year")
        if value is None or not year:
            continue
        data_date = dt.date(int(year), 12, 1)
        pub = dt.date(int(year) + 1, 1, 15).isoformat()
        rows.append((data_date.isoformat(), pub, value))
    if not rows:
        raise FetchError("NASS: zero usable corn-production rows")
    base.ensure_series_row(conn, "corn_production", entry,
                           "USDA NASS corn annual production")
    return base.insert_observations(conn, "corn_production", rows)


def _records(session, query) -> list:
    resp = session.get(API_URL, params={**query, "format": "JSON"},
                       timeout=120)
    if resp.status_code != 200:
        raise FetchError(f"NASS: HTTP {resp.status_code}")
    try:
        return resp.json()["data"]
    except (KeyError, ValueError, TypeError) as exc:
        raise FetchError("NASS: unexpected payload") from exc


def _num(value) -> float | None:
    """NASS values carry thousands commas and withheld markers (D)/(NA)/(Z)."""
    if not value:
        return None
    text = str(value).strip().replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


class _KeyedSession:
    def __init__(self, session: requests.Session, key: str):
        self._session, self._key = session, key

    def get(self, url, params, timeout):
        return self._session.get(url, params={**params, "key": self._key},
                                 timeout=timeout)
