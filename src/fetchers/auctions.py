"""Treasury auction fetcher — foreign-official demand (niche actor A2).

@context  The honest version of the government-flow signal R2's gold engine
          couldn't get from IMF data. Every Treasury NOTE auction publishes
          its bidder classes; the INDIRECT share (bids routed through the NY
          Fed — largely foreign official accounts) is the standard free proxy
          for foreign-government demand. Evidence (Fleming/SSRN): indirect
          bid explains ~75% of foreign 10-yr note purchases (notes only, not
          bills — so we keep NOTES). Source: Treasury Fiscal Data auctions
          API (free, no key). CONTEXT for the world picture, not a graded
          engine rule.
@done     fetch(): paged Note auctions from history_start; per auction_date
          the mean indirect share = indirect_accepted / total competitive
          accepted (primary + direct + indirect); stored as
          auction_indirect_share; pub_date = auction_date (published same
          day); idempotent; loud failures.
@todo     Per-tenor split (2/5/10-yr) if the blended share proves too coarse.
@limits   Notes only (bills' indirect share is a weak proxy). Multiple notes
          on one date are averaged. No key; append-only.
@affects  observations under auction_indirect_share; src/worldview foreign-
          demand line; weekly_run (source TREASURY).
"""

import datetime as dt
import sqlite3

import requests

from src.fetchers import base

API_URL = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service/"
           "v1/accounting/od/auctions_query")
FIELDS = ("auction_date,security_type,indirect_bidder_accepted,"
          "direct_bidder_accepted,primary_dealer_accepted")


class FetchError(RuntimeError):
    pass


def fetch(entry: dict, conn: sqlite3.Connection, session=None) -> int:
    session = session or requests.Session()
    start = entry["history_start"]
    since = start.isoformat() if isinstance(start, dt.date) else str(start)

    by_date: dict[str, list[float]] = {}
    for record in _records(session, since):
        if record.get("security_type") != "Note":
            continue  # notes only — bills' indirect share is a weak proxy
        share = _indirect_share(record)
        date = record.get("auction_date")
        if share is not None and date:
            by_date.setdefault(date, []).append(share)
    if not by_date:
        raise FetchError("TREASURY: zero usable note auctions")

    rows = [(date, date, sum(shares) / len(shares))
            for date, shares in sorted(by_date.items())]
    base.ensure_series_row(conn, "auction_indirect_share", entry,
                           "mean indirect-bidder share, Treasury notes")
    added = base.insert_observations(conn, "auction_indirect_share", rows)
    conn.commit()
    return added


def _indirect_share(r: dict) -> float | None:
    try:
        indirect = float(r["indirect_bidder_accepted"])
        direct = float(r["direct_bidder_accepted"])
        dealer = float(r["primary_dealer_accepted"])
    except (KeyError, TypeError, ValueError):
        return None
    total = indirect + direct + dealer
    return indirect / total if total > 0 else None


def _records(session, since: str) -> list:
    out, page = [], 1
    while True:
        resp = session.get(API_URL, params={
            "fields": FIELDS,
            "filter": f"auction_date:gte:{since}",
            "sort": "auction_date",
            "page[size]": 10000, "page[number]": page}, timeout=120)
        if resp.status_code != 200:
            raise FetchError(f"TREASURY: HTTP {resp.status_code}")
        try:
            payload = resp.json()
            data = payload["data"]
        except (KeyError, ValueError) as exc:
            raise FetchError("TREASURY: unexpected payload") from exc
        out += data
        if len(data) < 10000:
            return out
        page += 1
