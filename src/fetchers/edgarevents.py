"""EDGAR events fetcher — activist stakes + insider sale-intent (Tier-3, A4).

@context  Two niche event streams on the tracked equity universe (the same
          insider_tickers the Form-4 detector watches): Schedule 13D activist
          stakes (an investor taking >5% to PRESSURE a company — filed within
          ~10 days) and Form 144 sale-intent notices (an insider's advance
          notice before selling — an early bearish tell ahead of the Form 4).
          Both come from each company's EDGAR submissions index, no new key.
          CONTEXT for the world picture; not a graded rule. Honest scope: our
          equity universe is 10 chip names, and activists rarely target
          mega-cap chipmakers, so this fires rarely NOW — it scales with the
          equity themes (docs/ACTORS.md).
@done     fetch(): per universe ticker, recent SC 13D* and Form 144 filings
          within LOOKBACK_DAYS, stored as edgar_events rows (ticker, form,
          filing_date, accession); already-stored accessions skipped;
          idempotent; loud failures; reuses form4._cik_map.
@todo     13D amendment-timing escalation; 13D->outcome base rates (when the
          equity universe is broad enough to matter).
@limits   Live-only (no deep backfill). Declared UA per EDGAR policy.
@affects  edgar_events table; src/worldview event lines; weekly_run (source
          EDGAREVENTS).
"""

import datetime as dt
import sqlite3

import requests

from src.fetchers import form4

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
LOOKBACK_DAYS = 120
# form prefix -> the plain-language event label the world picture will use
WATCHED = {"SC 13D": "activist stake", "144": "insider sale-intent"}


class FetchError(RuntimeError):
    pass


def fetch(entry: dict, conn: sqlite3.Connection, session=None,
          today: dt.date | None = None) -> int:
    tickers = entry.get("insider_tickers") or []
    if not tickers:
        return 0  # universe empty -> designed no-op
    session = session or requests.Session()
    today = today or dt.date.today()
    floor = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    cik_map = form4._cik_map(session)
    seen = {r[0] for r in conn.execute(
        "SELECT DISTINCT accession FROM edgar_events")}

    added = 0
    for ticker in tickers:
        cik = cik_map.get(ticker.upper())
        if cik is None:
            raise FetchError(f"edgarevents: unknown ticker {ticker}")
        recent = form4._get_json(
            session, SUBMISSIONS_URL.format(cik=cik))["filings"]["recent"]
        for form, acc, fdate in zip(recent["form"], recent["accessionNumber"],
                                    recent["filingDate"]):
            if fdate < floor or acc in seen:
                continue
            label = _match(form)
            if label is None:
                continue
            cur = conn.execute(
                "INSERT OR IGNORE INTO edgar_events VALUES (?,?,?,?)",
                (ticker, label, fdate, acc))
            added += cur.rowcount
            seen.add(acc)
    conn.commit()
    return added


def _match(form: str) -> str | None:
    """13D and its amendments -> activist; exactly '144' -> sale-intent.
    (13G passive stakes and 144 amendments are excluded — not the signal.)"""
    form = form.strip()
    if form.startswith("SC 13D"):
        return "activist stake"
    if form == "144":
        return "insider sale-intent"
    return None


def recent_events(conn: sqlite3.Connection, as_of: str,
                  days: int = LOOKBACK_DAYS) -> dict[str, list[str]]:
    """{label: [tickers]} filed within `days` before as_of — for the world
    picture. As-of honest on the filing date."""
    floor = (dt.date.fromisoformat(as_of) - dt.timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT DISTINCT label, ticker FROM edgar_events"
        " WHERE filing_date <= ? AND filing_date >= ? ORDER BY ticker",
        (as_of, floor)).fetchall()
    out: dict[str, list[str]] = {}
    for label, ticker in rows:
        out.setdefault(label, []).append(ticker)
    return out
