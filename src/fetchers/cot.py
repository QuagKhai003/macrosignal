"""CFTC COT fetcher — speculator net positioning, Disaggregated + TFF reports.

@context  The party score's raw input (F8): speculator net = long - short per
          market. Commodities come from the Disaggregated report (party =
          Managed Money); Treasuries/currencies are NOT in it — they come from
          the Traders-in-Financial-Futures report (party = Leveraged Funds,
          the financial analog). Spec-gap fix, decision 2026-07-18. Source:
          cftc.gov raw files (the Socrata API is 403-blocked from here).
          Column indices verified against both annual files' header rows.
          Market codes + report type are config in signals.yaml, not code.
@done     fetch(): per report type, weekly file + last HISTORY_YEARS annual
          zips, parsed positionally, stored under cot_<market>; data_date =
          report Tuesday, pub_date = +pub_lag_days (Friday release);
          idempotent (overlaps dedupe on the observations PK); loud failures.
@todo     Phase 5: extend HISTORY_YEARS to 15+ (add the 2006-2016 combined
          archives) for the full replay window.
@limits   Positional format — the date-parse and zero-rows guards fail loud on
          any layout change rather than storing garbage. No API key.
@affects  weekly_run (1.5); observations under cot_gold, cot_wti, cot_ust10y,
          cot_eur, cot_corn; the Phase 2 party score.
"""

import csv
import datetime as dt
import io
import sqlite3
import zipfile

import requests

from src.fetchers import base

HISTORY_YEARS = 4  # current + 3 prior: covers the rolling3y party window
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; macrosignal research)"}
_COL_REPORT_DATE, _COL_CODE = 2, 3

# report type -> (weekly url, annual zip url template, long col, short col).
# Verified 2026-07-18 against the annual files' official header rows.
REPORTS = {
    "disagg": ("https://www.cftc.gov/dea/newcot/f_disagg.txt",
               "https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip",
               13, 14),   # M_Money_Positions_Long_All / Short_All
    "tff": ("https://www.cftc.gov/dea/newcot/FinFutWk.txt",
            "https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip",
            14, 15),      # Lev_Money_Positions_Long_All / Short_All
}


class FetchError(RuntimeError):
    pass


def fetch(entry: dict, conn: sqlite3.Connection, session=None,
          today: dt.date | None = None) -> int:
    session = session or requests.Session()
    today = today or dt.date.today()
    lag = dt.timedelta(days=int(entry["pub_lag_days"]))
    rows_by_sid: dict[str, list] = {sid: [] for sid in entry["markets"]}

    for report, (weekly_url, annual_url, col_long, col_short) in REPORTS.items():
        wanted = {m["code"]: sid for sid, m in entry["markets"].items()
                  if m["report"] == report}
        if not wanted:
            continue
        texts = [_annual_text(session, annual_url.format(year=year))
                 for year in range(today.year - HISTORY_YEARS + 1, today.year + 1)]
        texts.append(_get(session, weekly_url).text)
        for text in texts:
            for sid, data_date, net in _parse(text, wanted, col_long, col_short):
                pub = (dt.date.fromisoformat(data_date) + lag).isoformat()
                rows_by_sid[sid].append((data_date, pub, net))

    added = 0
    for sid, market in entry["markets"].items():
        if not rows_by_sid[sid]:
            raise FetchError(f"COT {sid}: zero rows parsed")
        base.ensure_series_row(conn, sid, entry,
                               f"CFTC {market['report']} contract {market['code']}"
                               f" (component of {entry['series_id']})")
        added += base.insert_observations(conn, sid, rows_by_sid[sid])
    conn.commit()
    return added


def _get(session, url):
    resp = session.get(url, headers=_HEADERS, timeout=120)
    if resp.status_code != 200:
        raise FetchError(f"COT fetch {url}: HTTP {resp.status_code}")
    return resp


def _annual_text(session, url: str) -> str:
    resp = _get(session, url)
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            # single-member archives; the member name differs per report type
            return zf.read(zf.namelist()[0]).decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, IndexError, KeyError) as exc:
        raise FetchError(f"COT {url}: bad annual archive") from exc


def _parse(text: str, wanted: dict[str, str], col_long: int, col_short: int):
    """Yield (series_id, iso_date, net) for wanted codes; header/malformed
    lines are skipped by the date-parse guard."""
    for row in csv.reader(io.StringIO(text)):
        if len(row) <= col_short:
            continue
        code = row[_COL_CODE].strip()
        if code not in wanted:
            continue
        data_date = row[_COL_REPORT_DATE].strip()
        try:
            dt.date.fromisoformat(data_date)
            net = float(row[col_long]) - float(row[col_short])
        except ValueError:
            continue
        yield wanted[code], data_date, net
