"""FRED fetcher — WALCL, WTREGEN, RRPONTSYD, DFII10, BAMLH0A0HYM2.

@context  Serves the registry entries net_liquidity (3 components), real_yields
          and credit_spread (tech spec Part 1 rows 1-4, 7, 9). Which FRED codes
          an entry needs is config, not code: the `series_codes` list on the
          signals.yaml entry.
@done     fetch(entry, conn, session=None): pulls each code from the FRED
          observations API, stores under fred_<code>, both dates (pub_date =
          data_date + pub_lag_days — FRED's API lacks per-obs publication
          dates; the registry lag is the honest deterministic stand-in),
          skips missing values ("."), idempotent via base.insert_observations.
@todo     Phase 5 may need ALFRED vintage dates for stricter as-of replay.
@limits   Raises FetchError on missing key / HTTP / shape problems — never
          swallows. Requires FRED_API_KEY unless a session is injected (tests).
@affects  weekly_run (1.5); observations under series_ids fred_walcl,
          fred_wtregen, fred_rrpontsyd, fred_dfii10, fred_bamlh0a0hym2.
"""

import datetime as dt
import sqlite3

import requests

from src import config
from src.fetchers import base

API_URL = "https://api.stlouisfed.org/fred/series/observations"


class FetchError(RuntimeError):
    pass


def fetch(entry: dict, conn: sqlite3.Connection, session=None) -> int:
    if session is None:
        key = config.get_key("FRED_API_KEY")
        if not key:
            raise FetchError("FRED_API_KEY missing from environment/.env")
        session = _KeyedSession(requests.Session(), key)

    lag = dt.timedelta(days=int(entry["pub_lag_days"]))
    added = 0
    for code in entry["series_codes"]:
        rows = _rows(session, code, lag)  # fetch BEFORE registering the series
        series_id = f"fred_{code.lower()}"
        base.ensure_series_row(conn, series_id, entry,
                               f"FRED {code} (component of {entry['series_id']})")
        added += base.insert_observations(conn, series_id, rows)
    conn.commit()
    return added


def _rows(session, code: str, lag: dt.timedelta) -> list[tuple[str, str, float]]:
    resp = session.get(API_URL, params={"series_id": code, "file_type": "json"},
                       timeout=60)
    if resp.status_code != 200:
        raise FetchError(f"FRED {code}: HTTP {resp.status_code}")
    try:
        observations = resp.json()["observations"]
    except (KeyError, ValueError) as exc:
        raise FetchError(f"FRED {code}: unexpected payload") from exc
    rows = []
    for obs in observations:
        if obs["value"] in (".", ""):
            continue  # FRED's missing-value marker
        pub = (dt.date.fromisoformat(obs["date"]) + lag).isoformat()
        rows.append((obs["date"], pub, float(obs["value"])))
    return rows


class _KeyedSession:
    """Adds the api_key param to every call; keeps the key out of call sites."""

    def __init__(self, session: requests.Session, key: str):
        self._session, self._key = session, key

    def get(self, url, params, timeout):
        return self._session.get(url, params={**params, "api_key": self._key},
                                 timeout=timeout)
