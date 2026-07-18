"""EIA fetcher — weekly petroleum series via the v2 seriesid API.

@context  Oil's engine inputs (tech spec Part 1 row 8 + F9): WCESTUS1 weekly
          crude stocks, and the R3 futures-curve contracts RCLC1/RCLC4 (daily,
          EIA stopped updating them 2024-04 — history feeds the replay; a live
          continuation is only worth building if the curve leg survives).
          The v2 `seriesid` route keeps the old-style series codes.
@done     fetch(): a single-code entry stores under the entry's own series_id
          (oil_inventories, unchanged); a multi-code entry stores per code
          under eia_<code-tail> (the FRED pattern — eia_rclc1, eia_rclc4);
          data_date = period, pub_date = +pub_lag_days; idempotent; loud
          failures.
@todo     More EIA series (natural gas) only via new signals.yaml entries.
@limits   Requires EIA_API_KEY unless a session is injected (tests). Raises
          FetchError — the orchestrator journals it.
@affects  weekly_run; observations under oil_inventories; src/drivers.py.
"""

import datetime as dt
import sqlite3

import requests

from src import config
from src.fetchers import base

API_URL = "https://api.eia.gov/v2/seriesid/{code}"


class FetchError(RuntimeError):
    pass


def fetch(entry: dict, conn: sqlite3.Connection, session=None) -> int:
    if session is None:
        key = config.get_key("EIA_API_KEY")
        if not key:
            raise FetchError("EIA_API_KEY missing from environment/.env")
        session = _KeyedSession(requests.Session(), key)

    lag = dt.timedelta(days=int(entry["pub_lag_days"]))
    added = 0
    codes = entry["series_codes"]
    for code in codes:
        rows = _rows(session, code, lag)
        sid = entry["series_id"] if len(codes) == 1 \
            else f"eia_{code.split('.')[1].lower()}"
        base.ensure_series_row(conn, sid, entry, f"EIA {code}")
        added += base.insert_observations(conn, sid, rows)
    conn.commit()
    return added


def _rows(session, code: str, lag: dt.timedelta) -> list[tuple[str, str, float]]:
    resp = session.get(API_URL.format(code=code),
                       params={"length": "6000"}, timeout=120)
    if resp.status_code != 200:
        raise FetchError(f"EIA {code}: HTTP {resp.status_code}")
    try:
        records = resp.json()["response"]["data"]
        rows = []
        for r in records:
            if r["value"] is None:
                continue
            data_date = r["period"]
            pub = (dt.date.fromisoformat(data_date) + lag).isoformat()
            rows.append((data_date, pub, float(r["value"])))
    except (KeyError, TypeError, ValueError) as exc:
        raise FetchError(f"EIA {code}: unexpected payload") from exc
    if not rows:
        raise FetchError(f"EIA {code}: zero rows returned")
    return rows


class _KeyedSession:
    def __init__(self, session: requests.Session, key: str):
        self._session, self._key = session, key

    def get(self, url, params, timeout):
        return self._session.get(url, params={**params, "api_key": self._key},
                                 timeout=timeout)
