"""Form-4 insider fetcher — open-market buys in the F13 theme universe.

@context  The insider-cluster rule (F13, src/insiders.py) shipped pure with
          an empty universe; the user chose SEMICONDUCTORS (2026-07-19), so
          this is its data arm. Per universe ticker: EDGAR submissions →
          recent Form 4 filings → the raw XML instance → non-derivative
          OPEN-MARKET PURCHASES only (transactionCode P, acquired A). Each
          buy row carries the FILING date alongside the transaction date —
          the as-of discipline for a signal that exists only when filed.
@done     fetch(): ticker→CIK via company_tickers.json (one request, cached
          per call); LOOKBACK_DAYS window; already-stored accessions skipped
          before any download (idempotent + cheap weekly); xsl-wrapped
          primaryDocument paths unwrapped; sales/derivatives/amendment
          oddities skipped silently (an empty week is normal, not an error);
          SEC pacing via PAUSE_SECONDS.
@todo     Equity engine (F9 earnings) + semis state-machine entry — the next
          semis batch.
@limits   Live-only signal: no deep backfill (Form 4 archives per ticker are
          thousands of files) — clusters grade from live operation onward,
          like news. Declared User-Agent per EDGAR policy.
@affects  insider_buys table; src/insiders.current_flags; the weekly report.
"""

import datetime as dt
import sqlite3
import time
import xml.etree.ElementTree as ET

import requests

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
DOC_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}/{doc}"
_HEADERS = {"User-Agent": "macrosignal personal research quangngokhai@gmail.com"}
LOOKBACK_DAYS = 180   # 2x the 90-day cluster window
MAX_FILINGS_PER_TICKER = 40  # newest first; a hot quarter stays covered
PAUSE_SECONDS = 0.15  # SEC fair-access pacing


class FetchError(RuntimeError):
    pass


def fetch(entry: dict, conn: sqlite3.Connection, session=None,
          today: dt.date | None = None) -> int:
    tickers = entry.get("insider_tickers") or []
    if not tickers:
        return 0  # universe empty — designed no-op
    session = session or requests.Session()
    today = today or dt.date.today()
    floor = (today - dt.timedelta(days=LOOKBACK_DAYS)).isoformat()
    cik_map = _cik_map(session)
    seen = {row[0] for row in conn.execute(
        "SELECT DISTINCT accession FROM insider_buys")}

    added = 0
    for ticker in tickers:
        cik = cik_map.get(ticker.upper())
        if cik is None:
            raise FetchError(f"form4: unknown ticker {ticker}")
        subs = _get_json(session, SUBMISSIONS_URL.format(cik=cik))
        recent = subs["filings"]["recent"]
        filings = [
            (acc, fdate, doc) for form, acc, fdate, doc in
            zip(recent["form"], recent["accessionNumber"],
                recent["filingDate"], recent["primaryDocument"])
            if form == "4" and fdate >= floor
        ][:MAX_FILINGS_PER_TICKER]
        for acc, filing_date, doc in filings:
            if acc in seen:
                continue
            buys = _parse_form4(_get_doc(session, cik, acc, doc))
            for buyer, trans_date in buys:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO insider_buys VALUES (?,?,?,?,?)",
                    (ticker, buyer, trans_date, filing_date, acc))
                added += cur.rowcount
            seen.add(acc)
    conn.commit()
    return added


def _parse_form4(xml_text: str) -> list[tuple[str, str]]:
    """(buyer, transaction_date) per open-market purchase — transactionCode
    P AND acquired 'A', non-derivative table only. Unparseable filings
    return [] (variant layouts are skipped, never guessed at)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    owners = [o.findtext("reportingOwnerId/rptOwnerName", "").strip()
              for o in root.iter("reportingOwner")]
    owners = [o for o in owners if o]
    if not owners:
        return []
    out = []
    for txn in root.iter("nonDerivativeTransaction"):
        code = txn.findtext("transactionCoding/transactionCode", "").strip()
        acquired = txn.findtext(
            "transactionAmounts/transactionAcquiredDisposedCode/value",
            "").strip()
        date = txn.findtext("transactionDate/value", "").strip()
        if code == "P" and acquired == "A" and date:
            out.extend((owner, date) for owner in owners)
    return out


def _cik_map(session) -> dict:
    data = _get_json(session, TICKERS_URL)
    return {row["ticker"].upper(): f"{int(row['cik_str']):010d}"
            for row in data.values()}


def _get_json(session, url):
    time.sleep(PAUSE_SECONDS)
    resp = session.get(url, headers=_HEADERS, timeout=60)
    if resp.status_code != 200:
        raise FetchError(f"form4: HTTP {resp.status_code} for {url}")
    try:
        return resp.json()
    except ValueError as exc:
        raise FetchError(f"form4: unexpected payload at {url}") from exc


def _get_doc(session, cik: str, acc: str, doc: str) -> str:
    if "/" in doc:  # xsl-rendered path — the raw XML sits beside it
        doc = doc.rsplit("/", 1)[1]
    url = DOC_URL.format(cik_int=int(cik), acc=acc.replace("-", ""), doc=doc)
    time.sleep(PAUSE_SECONDS)
    resp = session.get(url, headers=_HEADERS, timeout=60)
    if resp.status_code != 200:
        raise FetchError(f"form4: HTTP {resp.status_code} for {url}")
    return resp.text
