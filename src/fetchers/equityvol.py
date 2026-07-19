"""Equity-universe volume fetcher — wolf-pack turnover signal (Tier-C, R2).

@context  Research Round 2 (verified, Mgmt Science): the informal wolf-pack
          signal is measurable BEFORE a 13D — on the trigger day, share
          turnover reaches ~325% of normal, dominated by non-lead buyers
          accumulating. This module fetches weekly share VOLUME for the
          equity universe (the insider_tickers) so src/insiders can flag
          unusual turnover. Delegated from edgar.fetch (same universe as
          Form 4). CONTEXT for the world picture; not a graded rule.
@done     fetch(): per universe ticker, weekly-summed daily volume from the
          Yahoo chart API → vol_<ticker>; idempotent; loud failures.
@todo     Combine turnover spikes with a live 13D event → "wolf pack forming"
          (needs the universe to include activist-target names to fire).
@limits   HONEST SCOPE: mega-cap chips are rarely activist targets, so the
          combined 13D+turnover flag will seldom fire NOW — it scales with
          equity themes. Learning-grade Yahoo volume.
@affects  observations under vol_<ticker>; src/insiders.turnover_spikes;
          the weekly report.
"""

import datetime as dt
import sqlite3

import requests

from src.fetchers import base

API_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; macrosignal research)"}
LOOKBACK_DAYS = 400  # ~a year of weekly bars for the trailing-average baseline


def fetch(entry: dict, conn: sqlite3.Connection, session=None,
          today: dt.date | None = None) -> int:
    tickers = entry.get("insider_tickers") or []
    if not tickers:
        return 0  # universe empty — designed no-op
    session = session or requests.Session()
    today = today or dt.date.today()
    since = int(dt.datetime(
        (today - dt.timedelta(days=LOOKBACK_DAYS)).year,
        (today - dt.timedelta(days=LOOKBACK_DAYS)).month,
        (today - dt.timedelta(days=LOOKBACK_DAYS)).day,
        tzinfo=dt.UTC).timestamp())

    added = 0
    for ticker in tickers:
        weekly = _weekly_volume(session, ticker, since)
        if not weekly:
            continue
        sid = f"vol_{ticker.lower()}"
        conn.execute("INSERT OR IGNORE INTO series VALUES (?, 'Yahoo', '',"
                     " 'weekly', 'rolling3y', 'weekly share volume')", (sid,))
        rows = [(week, week, vol) for week, vol in sorted(weekly.items())]
        added += base.insert_observations(conn, sid, rows)
    conn.commit()
    return added


def _weekly_volume(session, symbol: str, since: int) -> dict:
    """{iso_week_friday: summed daily volume}. Empty on any fetch/shape
    failure (equity volume is a soft signal — never crash the run)."""
    try:
        resp = session.get(API_URL.format(symbol=symbol),
                           params={"period1": since, "period2": "9999999999",
                                   "interval": "1d"},
                           headers=_HEADERS, timeout=120)
        if resp.status_code != 200:
            return {}
        result = resp.json()["chart"]["result"][0]
        offset = int(result["meta"].get("gmtoffset", 0))
        timestamps = result["timestamp"]
        volumes = result["indicators"]["quote"][0]["volume"]
    except (KeyError, IndexError, TypeError, ValueError):
        return {}
    weekly: dict[str, float] = {}
    for ts, vol in zip(timestamps, volumes):
        if vol is None:
            continue
        day = dt.datetime.fromtimestamp(ts + offset, tz=dt.UTC).date()
        iso = day.isocalendar()
        # key each week by its Thursday (stable, timezone-safe)
        thursday = dt.date.fromisocalendar(iso.year, iso.week, 4).isoformat()
        weekly[thursday] = weekly.get(thursday, 0.0) + float(vol)
    return weekly
