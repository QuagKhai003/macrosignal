"""Whale-ledger fetcher tests (actors A1).

@context  13F-HR totals per named whale → whale_<name>_13f_total →
          report ledger lines. Context only; graded criteria untouched.
@done     Total from infotable xml, stored-period skip + idempotency,
          variant-layout skip, ledger latest/prior + as-of, report lines.
@todo     —
@limits   Offline via url-keyed fake session.
@affects  src/fetchers/whales.py, src/report.py.
"""

import pytest

from src import db, report
from src.fetchers import whales

ENTRY = {
    "series_id": "whale_ledger", "source": "EDGAR13F",
    "source_url": "https://example.com", "schedule": "quarterly",
    "window": "rolling20y", "pub_lag_days": 0,
    "whales": {"baupost": "0001061768"},
}

SUBMISSIONS = {"filings": {"recent": {
    "form": ["13F-HR", "10-K", "13F-HR"],
    "accessionNumber": ["0001-26-000200", "0001-26-000150", "0001-26-000100"],
    "filingDate": ["2026-05-10", "2026-03-01", "2026-02-10"],
}}}
INDEX = {"directory": {"item": [{"name": "primary_doc.xml"},
                                {"name": "53405.xml"}]}}
XML = "<t><value>1000</value><value>2500</value></t>"


class FakeSession:
    def __init__(self, overrides=None):
        self._o = overrides or {}

    def get(self, url, headers, timeout):
        payload = None
        for key, p in self._o.items():
            if key in url:
                payload = p
                break
        if payload is None:
            if "submissions" in url:
                payload = SUBMISSIONS
            elif "index.json" in url:
                payload = INDEX
            elif url.endswith(".xml"):
                payload = XML

        class R:
            status_code = 200 if payload is not None else 404
            text = payload if isinstance(payload, str) else ""
            def json(self, _p=payload):
                return _p
        return R()


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def test_fetch_stores_quarter_totals(conn):
    added = whales.fetch(ENTRY, conn, session=FakeSession())
    assert added == 2  # two 13F-HRs, the 10-K ignored
    rows = conn.execute(
        "SELECT data_date, pub_date, value FROM observations WHERE series_id"
        " = 'whale_baupost_13f_total' ORDER BY data_date").fetchall()
    # filed 2026-02-10 -> period 2025-12-31; filed 2026-05-10 -> 2026-03-31;
    # raw 3500 < $100M -> read as thousands -> $3.5M... still < the filer
    # floor in dollars, so the rule scales it exactly once
    assert rows == [("2025-12-31", "2026-02-10", 3_500_000.0),
                    ("2026-03-31", "2026-05-10", 3_500_000.0)]


def test_dollar_scale_filings_not_rescaled(conn):
    big = "<t><value>9100000000</value></t>"  # $9.1B in plain dollars
    whales.fetch(ENTRY, conn, session=FakeSession({".xml": big}))
    v = conn.execute("SELECT value FROM observations WHERE data_date ="
                     " '2026-03-31'").fetchone()[0]
    assert v == 9.1e9


def test_fetch_idempotent(conn):
    whales.fetch(ENTRY, conn, session=FakeSession())
    assert whales.fetch(ENTRY, conn, session=FakeSession()) == 0


def test_namespaced_value_tags_parse(conn):
    ns_xml = ('<ns1:informationTable><ns1:infoTable><ns1:value> 700 '
              '</ns1:value></ns1:infoTable></ns1:informationTable>')
    added = whales.fetch(ENTRY, conn, session=FakeSession({".xml": ns_xml}))
    assert added == 2
    v = conn.execute("SELECT value FROM observations WHERE data_date ="
                     " '2026-03-31'").fetchone()[0]
    assert v == 700_000.0  # thousands rule applies


def test_pre_2023_periods_scale_thousands(conn):
    old = {"filings": {"recent": {
        "form": ["13F-HR"], "accessionNumber": ["0001-15-000001"],
        "filingDate": ["2015-02-10"]}}}
    added = whales.fetch(ENTRY, conn,
                         session=FakeSession({"submissions": old}))
    assert added == 1
    d, v = conn.execute("SELECT data_date, value FROM observations"
                        " WHERE series_id = 'whale_baupost_13f_total'"
                        ).fetchone()
    assert d == "2014-12-31" and v == 3_500_000.0  # 3500 thousands -> $


def test_variant_layout_skipped_not_guessed(conn):
    no_table = {"directory": {"item": [{"name": "primary_doc.xml"}]}}
    added = whales.fetch(ENTRY, conn,
                         session=FakeSession({"index.json": no_table}))
    assert added == 0


def test_ledger_latest_prior_and_as_of(conn):
    whales.fetch(ENTRY, conn, session=FakeSession())
    conn.execute("UPDATE observations SET value = 5e9 WHERE data_date ="
                 " '2026-03-31'")  # distinct quarters for the direction test
    led = whales.ledger(conn, "2026-07-19", ENTRY["whales"])
    assert led == [{"name": "baupost", "period": "2026-03-31",
                    "total": 5e9, "prior": 3_500_000.0}]
    # before the newest filing is public, only the older quarter shows
    led = whales.ledger(conn, "2026-03-01", ENTRY["whales"])
    assert led[0]["period"] == "2025-12-31" and led[0]["prior"] is None


def test_report_ledger_lines():
    text = report.build({}, {}, "2026-W29", full=True, whale_ledger=[
        {"name": "baupost", "period": "2026-03-31", "total": 5.0e9,
         "prior": 4.0e9},
        {"name": "soros", "period": "2026-03-31", "total": 6.0e9,
         "prior": 8.0e9}])
    assert "1 of 2 tracked whales grew" in text
    assert "baupost: $5.0B" in text and "(+25% vs prior quarter)" in text
    assert "soros: $6.0B" in text and "(-25% vs prior quarter)" in text
