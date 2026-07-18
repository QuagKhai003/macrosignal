"""GDELT fetcher — theme news volume + weekly headlines.

@context  The crowding meter's raw material (F6/F7): per-theme weekly article
          counts (volume) and the trailing week's headlines (for the caged
          classifier). Queries are config in signals.yaml `themes:`. GDELT
          throttles hard (~1 request / 5s, 429 otherwise) — calls are paced
          and retried with backoff; a weekly batch does not care about 60s.
@done     fetch(): per theme, timelinevolraw daily counts -> COMPLETED ISO
          weeks stored under news_vol_<theme> (current week grows, so it is
          never frozen half-full); artlist trailing-week headlines ->
          headlines table (UNIQUE-deduped, label NULL). fetch_window():
          arbitrary past window for backfills/acceptance replays.
@todo     Phase 6 expansion: more themes = more yaml lines.
@limits   No key. Counts are GDELT's coverage, not ground truth — used only
          as a ratio vs their own trailing average (F6). Raises FetchError
          after retries; orchestrator journals it.
@affects  weekly_run; observations news_vol_*; headlines table;
          src/newsscore.py (4.3).
"""

import datetime as dt
import sqlite3
import time

import requests

from src.fetchers import base

API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
_HEADERS = {"User-Agent": "macrosignal personal research (quangngokhai@gmail.com)"}
_TRIES = 4
_BACKOFFS_S = (30, 60, 120)  # escalating: GDELT's penalty box grows on repeats
_BACKOFF_S = _BACKOFFS_S[0]  # (kept as the first step for test assertions)
_PACE_S = 6      # GDELT's own rule: one request per 5 seconds — pace EVERY call
VOLUME_LOOKBACK_DAYS = 400  # > 52 weeks for the F6 trailing mean
MAX_HEADLINES = 100


class FetchError(RuntimeError):
    pass


def fetch(entry: dict, conn: sqlite3.Connection, session=None,
          today: dt.date | None = None, pause=time.sleep) -> int:
    session = session or requests.Session()
    today = today or dt.date.today()
    added = 0
    for theme, query in entry["themes"].items():
        if _week_already_fetched(conn, theme, today):
            continue  # same-week re-runs make ZERO GDELT calls
        daily = _timeline(session, query, today, pause)  # fetch BEFORE
        articles = _articles(session, query,             # registering series
                             today - dt.timedelta(days=7), today, pause)
        sid = f"news_vol_{theme}"
        base.ensure_series_row(conn, sid, entry,
                               f"weekly article count (component of"
                               f" {entry['series_id']})")
        added += _store_weekly_volumes(conn, sid, daily, today)
        added += _store_headlines(conn, theme, articles)
    conn.commit()
    return added


def fetch_window(entry: dict, conn: sqlite3.Connection, theme: str,
                 start: dt.date, end: dt.date, session=None,
                 pause=time.sleep) -> int:
    """Backfill one theme's headlines for an arbitrary past window."""
    session = session or requests.Session()
    rows = _articles(session, entry["themes"][theme], start, end, pause)
    added = _store_headlines(conn, theme, rows)
    conn.commit()
    return added


def _week_already_fetched(conn, theme: str, today: dt.date) -> bool:
    """True when this week's GDELT work is already stored: the last COMPLETED
    ISO week's volume row exists and the trailing week has headlines. Makes
    re-runs free and keeps us out of the rate limiter entirely."""
    iso = today.isocalendar()
    last_sunday = (today - dt.timedelta(days=iso.weekday))
    vol = conn.execute(
        "SELECT 1 FROM observations WHERE series_id = ? AND data_date = ?",
        (f"news_vol_{theme}", last_sunday.isoformat())).fetchone()
    if vol is None:
        return False
    heads = conn.execute(
        "SELECT COUNT(*) FROM headlines WHERE theme = ? AND seen_date > ?",
        (theme, (today - dt.timedelta(days=7)).isoformat())).fetchone()[0]
    return heads > 0


def _store_weekly_volumes(conn, sid, daily_points, today) -> int:
    weekly: dict[tuple, float] = {}
    week_end: dict[tuple, dt.date] = {}
    for date_str, value in daily_points:
        day = dt.date.fromisoformat(date_str[:10].replace("/", "-"))
        iso = day.isocalendar()
        key = (iso.year, iso.week)
        weekly[key] = weekly.get(key, 0.0) + value
        sunday = day + dt.timedelta(days=7 - iso.weekday)
        week_end[key] = sunday
    rows = [(week_end[k].isoformat(), week_end[k].isoformat(), v)
            for k, v in weekly.items() if week_end[k] < today]  # completed only
    return base.insert_observations(conn, sid, rows)


def _store_headlines(conn, theme, articles) -> int:
    added = 0
    for title, seen_date, url in articles:
        cur = conn.execute(
            "INSERT OR IGNORE INTO headlines (theme, seen_date, title,"
            " source_url) VALUES (?, ?, ?, ?)", (theme, seen_date, title, url))
        added += cur.rowcount
    return added


def _timeline(session, query, today, pause):
    start = today - dt.timedelta(days=VOLUME_LOOKBACK_DAYS)
    payload = _get(session, {
        "query": f"{_grouped(query)} sourcelang:english",
        "mode": "timelinevolraw",
        "format": "json", "startdatetime": _stamp(start),
        "enddatetime": _stamp(today)}, pause)
    series = payload.get("timeline", [])
    if not series:
        raise FetchError("GDELT: empty volume timeline")
    return [(p["date"][:4] + "-" + p["date"][4:6] + "-" + p["date"][6:8],
             float(p["value"])) for p in series[0]["data"]]


def _articles(session, query, start, end, pause):
    payload = _get(session, {
        "query": f"{_grouped(query)} sourcelang:english", "mode": "artlist",
        "format": "json", "maxrecords": str(MAX_HEADLINES),
        "startdatetime": _stamp(start), "enddatetime": _stamp(end)}, pause)
    out = []
    for a in payload.get("articles", []):
        title = (a.get("title") or "").strip()
        seen = a.get("seendate", "")[:8]
        if title and len(seen) == 8:
            out.append((title, f"{seen[:4]}-{seen[4:6]}-{seen[6:]}",
                        a.get("url", "")))
    return out


def _get(session, params, pause) -> dict:
    last = "no response"
    for attempt in range(_TRIES):
        pause(_PACE_S)  # proactive pacing: never hit the 1-per-5s wall
        resp = session.get(API_URL, params=params, headers=_HEADERS,
                           timeout=90)
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                body = (resp.text or "")[:100].replace("\n", " ")
                last = f"200 non-json: {body!r}"  # error text or throttle page
        else:
            last = f"HTTP {resp.status_code}"
        if attempt < _TRIES - 1:
            # honor the server's own Retry-After when it names one (docs:
            # 429s carry it); else escalate our guesses; cap at 3 minutes
            retry_after = 0
            try:
                retry_after = int(getattr(resp, "headers", {}).get(
                    "Retry-After", 0))
            except (TypeError, ValueError):
                pass
            wait = retry_after or _BACKOFFS_S[min(attempt,
                                                  len(_BACKOFFS_S) - 1)]
            pause(min(wait, 180))
    raise FetchError(f"GDELT: {last} after {_TRIES} tries")


def _grouped(query: str) -> str:
    """GDELT's parser: parentheses are REQUIRED around OR lists combined with
    filters, and FORBIDDEN around anything else."""
    return f"({query})" if " OR " in query else query


def _stamp(day: dt.date) -> str:
    return day.strftime("%Y%m%d") + "000000"
