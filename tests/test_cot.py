"""COT fetcher tests — offline via fake session serving canned files.

@context  Batch 1.3 acceptance: hand-read rows from both real report layouts
          (Disaggregated cols 13/14, TFF cols 14/15) parse to hand-computed
          nets; idempotent; loud failures.
@done     Net arithmetic + lag per report, header/garbage skipping, annual+
          weekly merge dedupe, per-report routing, idempotency, HTTP/zip/
          zero-row failures, live smoke across both reports.
@todo     —
@limits   Default run offline; integration test hits cftc.gov (8 zips, slow).
@affects  src/fetchers/cot.py.
"""

import datetime as dt
import io
import zipfile

import pytest

from src import db, registry
from src.fetchers import cot

ENTRY = {
    "series_id": "cot_managed_money", "source": "CFTC",
    "source_url": "https://example.com", "schedule": "weekly",
    "window": "rolling3y", "pub_lag_days": 3,
    "markets": {"cot_gold": {"code": "088691", "report": "disagg"},
                "cot_ust10y": {"code": "043602", "report": "tff"}},
}
TODAY = dt.date(2026, 7, 18)


def disagg_line(code, date, long_pos, short_pos):
    # Disaggregated layout: MM long/short at cols 13/14
    row = ["MKT - EXCHANGE", "260714", date, code, "CBT", "00", "001",
           "429298", "1", "2", "3", "4", "5", str(long_pos), str(short_pos), "6"]
    return ",".join(row)


def tff_line(code, date, long_pos, short_pos):
    # TFF layout: Lev Money long/short at cols 14/15 (Dealer block is 3 wide)
    row = ["MKT - EXCHANGE", "260714", date, code, "CME", "00", "099",
           "700000", "1", "2", "3", "4", "5", "6", str(long_pos), str(short_pos),
           "7"]
    return ",".join(row)


DISAGG_WEEKLY = "\n".join([
    disagg_line("088691", "2026-07-14", 136905, 16126),  # gold net = +120779
    disagg_line("999999", "2026-07-14", 1, 1),           # unwanted market
    "short,garbage,row",
])
DISAGG_ANNUAL = "\n".join([
    "Market_and_Exchange_Names,As_of,Report_Date,Code," + "x," * 11 + "x",
    disagg_line("088691", "2026-07-07", 130000, 20000),  # gold net = +110000
    disagg_line("088691", "2026-07-14", 136905, 16126),  # overlap: dedupe
])
TFF_WEEKLY = tff_line("043602", "2026-07-14", 387963, 2467616)  # net = -2079653
TFF_ANNUAL = "\n".join([
    "Market_and_Exchange_Names,As_of,Report_Date,Code," + "x," * 12 + "x",
    tff_line("043602", "2026-07-07", 400000, 2400000),   # net = -2000000
])


def as_zip(text: str, member: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, text)
    return buf.getvalue()


class FakeResponse:
    def __init__(self, status=200, text="", content=b""):
        self.status_code, self.text, self.content = status, text, content


class FakeSession:
    def __init__(self, status=200):
        self._status = status
        self.calls = []

    def get(self, url, headers, timeout):
        self.calls.append(url)
        if self._status != 200:
            return FakeResponse(status=self._status)
        if "disagg_txt" in url:
            return FakeResponse(content=as_zip(DISAGG_ANNUAL, "f_year.txt"))
        if url.endswith(".zip"):  # TFF annual or combined archive
            return FakeResponse(content=as_zip(TFF_ANNUAL, "FinFutYY.txt"))
        if url == cot.REPORTS["disagg"][0]:
            return FakeResponse(text=DISAGG_WEEKLY)
        return FakeResponse(text=TFF_WEEKLY)


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def test_both_reports_parse_merge_and_lag(conn):
    added = cot.fetch(ENTRY, conn, session=FakeSession(), today=TODAY)
    assert added == 4  # 2 gold + 2 ust10y after dedupe
    rows = conn.execute(
        "SELECT series_id, data_date, pub_date, value FROM observations"
        " ORDER BY series_id, data_date").fetchall()
    assert rows == [
        ("cot_gold", "2026-07-07", "2026-07-10", 110000.0),
        ("cot_gold", "2026-07-14", "2026-07-17", 120779.0),
        ("cot_ust10y", "2026-07-07", "2026-07-10", -2000000.0),
        ("cot_ust10y", "2026-07-14", "2026-07-17", -2079653.0)]


def test_fetches_hist_archive_plus_annuals_plus_weeklies(conn):
    session = FakeSession()
    cot.fetch(ENTRY, conn, session=session, today=TODAY)
    disagg_zips = [u for u in session.calls
                   if "disagg_txt" in u and u.endswith(".zip")]
    tff_zips = [u for u in session.calls
                if "disagg_txt" not in u and u.endswith(".zip")]
    # 2011..2016 come from ONE combined archive; 2017..2026 are annual
    expected = 1 + (TODAY.year - cot.FIRST_ANNUAL_YEAR + 1)
    assert len(disagg_zips) == expected
    assert len(tff_zips) == expected
    assert disagg_zips[0].endswith("hist_2006_2016.zip")
    assert disagg_zips[1].endswith("2017.zip")
    assert disagg_zips[-1].endswith("2026.zip")
    assert cot.REPORTS["disagg"][0] in session.calls
    assert cot.REPORTS["tff"][0] in session.calls


def test_hist_archive_date_format_parses(conn):
    # the combined 2006-2016 archives use US datetime strings, floats too
    line = tff_line("043602", "12/27/2016 12:00:00 AM", 0, 0).replace(
        ",0,0,", ",422678.000000,613501.000000,")
    parsed = list(cot._parse(line, {"043602": "cot_ust10y"}, 14, 15))
    assert parsed == [("cot_ust10y", "2016-12-27", -190823.0)]


def test_idempotent(conn):
    cot.fetch(ENTRY, conn, session=FakeSession(), today=TODAY)
    assert cot.fetch(ENTRY, conn, session=FakeSession(), today=TODAY) == 0


def test_http_error_raises(conn):
    with pytest.raises(cot.FetchError, match="HTTP 403"):
        cot.fetch(ENTRY, conn, session=FakeSession(status=403), today=TODAY)


def test_bad_zip_raises(conn):
    class BadZipSession(FakeSession):
        def get(self, url, headers, timeout):
            if url.endswith(".zip"):
                return FakeResponse(content=b"not a zip")
            return FakeResponse(text=DISAGG_WEEKLY)
    with pytest.raises(cot.FetchError, match="bad annual archive"):
        cot.fetch(ENTRY, conn, session=BadZipSession(), today=TODAY)


def test_zero_rows_for_a_market_raises(conn):
    class GoldOnlySession(FakeSession):
        def get(self, url, headers, timeout):
            if "disagg_txt" not in url and url.endswith(".zip"):
                return FakeResponse(content=as_zip("", "FinFutYY.txt"))
            if url == cot.REPORTS["tff"][0]:
                return FakeResponse(text="")
            return super().get(url, headers, timeout)
    with pytest.raises(cot.FetchError, match="cot_ust10y: zero rows"):
        cot.fetch(ENTRY, conn, session=GoldOnlySession(), today=TODAY)


@pytest.mark.integration
def test_live_all_five_markets(conn):
    entry = next(e for e in registry.load_registry()
                 if e["series_id"] == "cot_managed_money")
    cot.fetch(entry, conn, session=None)
    for sid in ("cot_gold", "cot_wti", "cot_ust10y", "cot_eur", "cot_corn"):
        n = conn.execute("SELECT COUNT(*) FROM observations"
                         " WHERE series_id = ?", (sid,)).fetchone()[0]
        latest = conn.execute("SELECT MAX(data_date) FROM observations"
                              " WHERE series_id = ?", (sid,)).fetchone()[0]
        assert n > 150, sid   # ~52/year x 4 years
        assert latest >= "2026-07-01", sid
