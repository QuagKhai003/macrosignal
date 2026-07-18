"""XBRL earnings fetcher — annual net income for the equity theme (F9).

@context  The semis "is it cheap?" engine needs a decade of real profits.
          Source: SEC XBRL companyconcept API (free, official) — one JSON per
          universe ticker with every filed us-gaap:NetIncomeLoss value. We
          keep full-fiscal-year values from 10-K filings only; pub_date = the
          FILING date (true as-of — a year's profit exists for the machine
          once filed, ~2 months after year end).
@done     fetch(): ticker→CIK via the form4 map; FY/10-K filter; the same
          fiscal year re-appears in later 10-Ks as a comparative — the
          EARLIEST filing wins (that's when it became public); stored under
          earn_<ticker>; idempotent; loud failures.
@todo     —
@limits   XBRL coverage starts ~2009 → ~17 annual values per mature company.
          The F9 20-yr percentile window will answer None for several more
          years (F1's contribute-nothing rule) — the profits-rising leg and
          everything else works now; disclosed in RESEARCH/STATUS.
@affects  observations under earn_<ticker>; src/spine.derive_semis_earnings
          → derive_semis_valuation; src/drivers.semis_engine.
"""

import sqlite3
import time

import requests

from src.fetchers import base, form4

CONCEPT_URL = ("https://data.sec.gov/api/xbrl/companyconcept/"
               "CIK{cik}/us-gaap/NetIncomeLoss.json")
_HEADERS = {"User-Agent": "macrosignal personal research quangngokhai@gmail.com"}
PAUSE_SECONDS = 0.15


class FetchError(RuntimeError):
    pass


def fetch(entry: dict, conn: sqlite3.Connection, session=None) -> int:
    session = session or requests.Session()
    cik_map = form4._cik_map(session)
    added = 0
    for ticker in entry["tickers"]:
        cik = cik_map.get(ticker.upper())
        if cik is None:
            raise FetchError(f"earnings: unknown ticker {ticker}")
        rows = _annual_rows(_get_json(session, CONCEPT_URL.format(cik=cik)))
        if not rows:
            raise FetchError(f"earnings: zero FY rows for {ticker}")
        sid = f"earn_{ticker.lower()}"
        base.ensure_series_row(conn, sid, entry,
                               f"annual net income {ticker} (10-K XBRL)")
        added += base.insert_observations(conn, sid, rows)
    conn.commit()
    return added


def _annual_rows(payload) -> list[tuple[str, str, float]]:
    """(fiscal_year_end, first_filing_date, net_income) — FY values from
    10-K filings, earliest filing per fiscal-year end (comparative repeats
    in later 10-Ks are ignored: as-of = first publication)."""
    try:
        facts = payload["units"]["USD"]
    except (KeyError, TypeError):
        return []
    first: dict[str, tuple[str, float]] = {}
    for f in facts:
        if f.get("form") != "10-K" or f.get("fp") != "FY":
            continue
        end, filed, value = f.get("end"), f.get("filed"), f.get("val")
        if not end or not filed or value is None:
            continue
        if end not in first or filed < first[end][0]:
            first[end] = (filed, float(value))
    return [(end, filed, value)
            for end, (filed, value) in sorted(first.items())]


def _get_json(session, url):
    time.sleep(PAUSE_SECONDS)
    resp = session.get(url, headers=_HEADERS, timeout=60)
    if resp.status_code != 200:
        raise FetchError(f"earnings: HTTP {resp.status_code} for {url}")
    try:
        return resp.json()
    except ValueError as exc:
        raise FetchError(f"earnings: unexpected payload at {url}") from exc
