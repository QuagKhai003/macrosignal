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

HISTORY_YEARS = 16  # the 15-yr replay needs the party window answerable from
                    # 2011 (2011 - 3 yrs = 2008; combined archives cover it)

# pre-2017 years ship as one combined archive per report type
HIST_URLS = {
    "disagg": "https://www.cftc.gov/files/dea/history/fut_disagg_txt_hist_2006_2016.zip",
    "tff": "https://www.cftc.gov/files/dea/history/fin_fut_txt_2006_2016.zip",
}
FIRST_ANNUAL_YEAR = 2017
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; macrosignal research)"}
_COL_REPORT_DATE, _COL_CODE = 2, 3
# disaggregated concentration: gross top-4 traders' long/short share of OI
# (cols verified against the annual disagg header, 191 fields). The niche
# whale-concentration gauge — already inside the file we download (actor A3).
_CONC_LONG_4, _CONC_SHORT_4 = 161, 162

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

    disagg_texts = []
    for report, (weekly_url, annual_url, col_long, col_short) in REPORTS.items():
        wanted = {m["code"]: sid for sid, m in entry["markets"].items()
                  if m["report"] == report}
        if not wanted:
            continue
        first_year = today.year - HISTORY_YEARS + 1
        texts = []
        if first_year < FIRST_ANNUAL_YEAR:
            texts.append(_annual_text(session, HIST_URLS[report]))
        texts += [_annual_text(session, annual_url.format(year=year))
                  for year in range(max(first_year, FIRST_ANNUAL_YEAR),
                                    today.year + 1)]
        texts.append(_get(session, weekly_url).text)
        if report == "disagg":
            disagg_texts = texts  # reuse for concentration, no re-fetch
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
    added += _store_concentration(entry, conn, disagg_texts, lag)
    conn.commit()
    return added


def _store_concentration(entry, conn, disagg_texts, lag) -> int:
    """conc_<market> = the more-concentrated side's top-4-trader share of open
    interest (disaggregated markets only — the whale-concentration gauge, A3).
    Parses the disagg texts ALREADY fetched for positioning; no re-fetch."""
    wanted = {m["code"]: f"conc_{sid.split('_', 1)[1]}"
              for sid, m in entry["markets"].items()
              if m["report"] == "disagg"}
    if not wanted:
        return 0
    rows_by_sid: dict[str, list] = {sid: [] for sid in wanted.values()}
    for text in disagg_texts:
        for row in csv.reader(io.StringIO(text)):
            if len(row) <= _CONC_SHORT_4:
                continue
            sid = wanted.get(row[_COL_CODE].strip())
            if sid is None:
                continue
            date = _report_date(row[_COL_REPORT_DATE].strip())
            if date is None:
                continue
            try:
                conc = max(float(row[_CONC_LONG_4]), float(row[_CONC_SHORT_4]))
            except ValueError:
                continue
            pub = (dt.date.fromisoformat(date) + lag).isoformat()
            rows_by_sid[sid].append((date, pub, conc))
    added = 0
    for sid, rows in rows_by_sid.items():
        if not rows:
            continue
        conn.execute("INSERT OR IGNORE INTO series VALUES (?, 'CFTC', '',"
                     " 'weekly', 'rolling3y', 'top-4 trader concentration')",
                     (sid,))
        added += base.insert_observations(conn, sid, rows)
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
        data_date = _report_date(row[_COL_REPORT_DATE].strip())
        if data_date is None:
            continue
        try:
            net = float(row[col_long]) - float(row[col_short])
        except ValueError:
            continue
        yield wanted[code], data_date, net


def _report_date(raw: str) -> str | None:
    """ISO in annual/weekly files; '12/27/2016 12:00:00 AM' in the combined
    2006-2016 archives. Anything else (headers, garbage) -> None."""
    try:
        return dt.date.fromisoformat(raw).isoformat()
    except ValueError:
        pass
    try:
        return dt.datetime.strptime(raw, "%m/%d/%Y %I:%M:%S %p").date().isoformat()
    except ValueError:
        return None
