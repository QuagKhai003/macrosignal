"""ECB data-portal fetcher — euro-area 2-year market yield (research R4).

@context  The euro engine's driver swap (RESEARCH.md R4): DGS2 − ECBDFR
          (market vs POLICY rate) failed C1; the market-vs-market version
          needs a euro-area market 2y. Source: the ECB yield-curve dataset
          (AAA govt, Svensson 2y spot, daily since 2004-09), free CSV API,
          no key. Stored as ecb_yc2y; src/spine derives us_ez2y_diff =
          DGS2 − ecb_yc2y.
@done     fetch(): csvdata parse (TIME_PERIOD/OBS_VALUE), data_date = the
          quote date, pub_date = +pub_lag_days (published next business
          day); idempotent; loud failures.
@todo     —
@limits   AAA euro-area composite (Bunds dominate), not a single country —
          the cleanest free daily euro market rate. No network in tests
          (fake session).
@affects  weekly_run (source ECB); observations under ecb_yc2y;
          src/spine.derive_market_rate_differential; src/drivers (eur).
"""

import csv
import datetime as dt
import sqlite3

import requests

from src.fetchers import base


class FetchError(RuntimeError):
    pass


def fetch(entry: dict, conn: sqlite3.Connection, session=None) -> int:
    session = session or requests.Session()
    start = entry["history_start"]
    period = start.isoformat() if isinstance(start, dt.date) else str(start)
    resp = session.get(entry["source_url"],
                       params={"format": "csvdata", "startPeriod": period},
                       timeout=120)
    if resp.status_code != 200:
        raise FetchError(f"ECB: HTTP {resp.status_code}")

    lag = dt.timedelta(days=int(entry["pub_lag_days"]))
    rows = []
    for r in csv.DictReader(resp.text.splitlines()):
        try:
            day = r["TIME_PERIOD"]
            value = float(r["OBS_VALUE"])
            pub = (dt.date.fromisoformat(day) + lag).isoformat()
        except (KeyError, ValueError):
            continue  # confidential/empty observations are skipped, not zeroed
        rows.append((day, pub, value))
    if not rows:
        raise FetchError("ECB: zero usable yield rows")
    base.ensure_series_row(conn, "ecb_yc2y", entry,
                           "ECB euro-area AAA 2y spot yield (YC SR_2Y)")
    added = base.insert_observations(conn, "ecb_yc2y", rows)
    conn.commit()
    return added
