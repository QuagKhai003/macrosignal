"""IMF IRFCL fetcher — world central-bank gold flow (gold's dominant-flow leg).

@context  Research R2 (spec §3.3): central-bank gold accumulation is the
          documented breaker of gold's real-yields driver. Source: the IMF
          SDMX 2.1 API (free, monthly), IRFCL template gold volume in fine
          troy ounces (indicator IRFCLDT1_IRFCL56V_FTO, combined sector
          S1XS1311, ~88 reporters back to 1999). WGC's own CB statistics are
          built from this same template.
@done     fetch(): pull the all-country monthly gold-volume CSV, keep the
          combined sector, drop aggregate codes (G163 euro area — members
          already report individually), sum per-country month-over-month
          changes for ADJACENT months only (panel entries/exits contribute
          nothing — China appearing 2015-M06 is not a 53 Moz "purchase"),
          store the world flow in tonnes under cb_gold_flow with a maturity
          embargo: months whose pub_date has not passed are NOT stored, so
          INSERT OR IGNORE never freezes a still-filling reporting panel.
@todo     — (engine wiring KILLED by the R2 replay; series accumulates for
          future research only — see RESEARCH.md)
@limits   Reported holdings only — unreported stealth tranches (China between
          disclosures) appear late, when disclosed. This proved fatal: the
          2022-24 CB-era buying was mostly unreported, so the reported flow
          inverted the §3.3 thesis (strong 2008-14, quiet 2022-24).
@affects  weekly_run (source IMF); observations under cb_gold_flow;
          src/drivers.cb_flow_strong (the gold engine's OR leg).
"""

import csv
import datetime as dt
import sqlite3

import requests

from src.fetchers import base

SECTOR = "S1XS1311"  # monetary authorities + central government (the template line)
OZT_PER_TONNE = 32150.7466  # fine troy ounces per metric tonne


class FetchError(RuntimeError):
    pass


def fetch(entry: dict, conn: sqlite3.Connection, session=None,
          today: dt.date | None = None) -> int:
    if session is None:
        session = requests.Session()
        session.headers["Accept"] = "application/vnd.sdmx.data+csv;version=1.0.0"
    today = today or dt.date.today()
    lag = dt.timedelta(days=int(entry["pub_lag_days"]))

    start = entry["history_start"]
    period = f"{start.year}-{start.month:02d}" if isinstance(start, dt.date) \
        else str(start)[:7]
    resp = session.get(entry["source_url"], params={"startPeriod": period},
                       timeout=180)
    if resp.status_code != 200:
        raise FetchError(f"IMF: HTTP {resp.status_code}")

    flows = _monthly_world_flow(_holdings(resp.text))
    rows = []
    for month, oz in sorted(flows.items()):
        data_date = _month_end(month)
        pub = data_date + lag
        if pub > today:
            continue  # panel still filling — never freeze an immature month
        rows.append((data_date.isoformat(), pub.isoformat(),
                     oz / OZT_PER_TONNE))
    if not rows:
        raise FetchError("IMF: zero usable gold-flow months")
    base.ensure_series_row(conn, "cb_gold_flow", entry,
                           "world CB net gold purchases, tonnes/month (IRFCL)")
    added = base.insert_observations(conn, "cb_gold_flow", rows)
    conn.commit()
    return added


def _holdings(text: str) -> dict:
    """{country: {month_index: fine troy oz}} from the SDMX-CSV payload."""
    out: dict[str, dict[int, float]] = {}
    for row in csv.DictReader(text.splitlines()):
        country = row.get("COUNTRY", "")
        if row.get("SECTOR") != SECTOR:
            continue
        if not country.isalpha():
            continue  # aggregate codes (G163) double-count member states
        try:
            value = float(row["OBS_VALUE"])
            year, month = row["TIME_PERIOD"].split("-M")
            index = int(year) * 12 + int(month) - 1
        except (KeyError, ValueError):
            continue
        out.setdefault(country, {})[index] = value
    if not out:
        raise FetchError("IMF: unexpected payload (no template gold rows)")
    return out


def _monthly_world_flow(holdings: dict) -> dict:
    """{month_index: net oz change} summed over countries reporting BOTH the
    month and the one before it — panel entries/exits contribute nothing."""
    flows: dict[int, float] = {}
    for months in holdings.values():
        for index, value in months.items():
            if index - 1 in months:
                flows[index] = flows.get(index, 0.0) + value - months[index - 1]
    return flows


def _month_end(index: int) -> dt.date:
    year, month = divmod(index, 12)
    return dt.date(year + (month == 11), (month + 1) % 12 + 1, 1) \
        - dt.timedelta(days=1)
