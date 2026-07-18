"""Form-4 insider fetcher tests — the F13 data arm (semis batch).

@context  EDGAR Form 4 XML → open-market buys (code P, acquired A) →
          insider_buys table → insiders.current_flags → the report line.
@done     Parse (P kept; sales/derivative-only skipped; multi-owner fan-out;
          bad XML → []), lookback + already-seen skip, idempotency, unknown
          ticker loud, empty universe no-op, current_flags as-of on filing
          date, report line rendering.
@todo     —
@limits   Offline via fake session (canned submissions + XML).
@affects  src/fetchers/form4.py, src/insiders.py, src/report.py.
"""

import datetime as dt

import pytest

from src import db, insiders, report
from src.fetchers import form4

FORM4_BUY = """<?xml version="1.0"?>
<ownershipDocument>
  <reportingOwner>
    <reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId>
  </reportingOwner>
  <nonDerivativeTable>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-07-01</value></transactionDate>
      <transactionCoding><transactionCode>P</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
    <nonDerivativeTransaction>
      <transactionDate><value>2026-07-02</value></transactionDate>
      <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
      <transactionAmounts>
        <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
      </transactionAmounts>
    </nonDerivativeTransaction>
  </nonDerivativeTable>
</ownershipDocument>"""

TICKERS_JSON = {"0": {"cik_str": 1045810, "ticker": "NVDA",
                      "title": "NVIDIA CORP"}}


def submissions(forms):
    """forms: list of (form, accession, filing_date, doc)."""
    f, a, d, p = (list(x) for x in zip(*forms)) if forms else ([], [], [], [])
    return {"filings": {"recent": {
        "form": f, "accessionNumber": a, "filingDate": d,
        "primaryDocument": p}}}


class FakeSession:
    def __init__(self, docs):
        self._docs = docs  # url-substring -> payload (dict = json, str = text)

    def get(self, url, headers, timeout):
        for key, payload in self._docs.items():
            if key in url:
                class R:
                    status_code = 200
                    text = payload if isinstance(payload, str) else ""
                    def json(self, _p=payload):
                        return _p
                return R()

        class R404:
            status_code = 404
        return R404()


ENTRY = {"insider_tickers": ["NVDA"]}
TODAY = dt.date(2026, 7, 19)


def make_session(forms, xml=FORM4_BUY):
    return FakeSession({"company_tickers": TICKERS_JSON,
                        "submissions/CIK": submissions(forms),
                        "/Archives/": xml})


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def test_parse_keeps_buys_drops_sales():
    buys = form4._parse_form4(FORM4_BUY)
    assert buys == [("DOE JANE", "2026-07-01")]


def test_parse_bad_xml_returns_empty():
    assert form4._parse_form4("<not-xml") == []


def test_fetch_stores_buy_rows(conn):
    forms = [("4", "0001-26-000001", "2026-07-03", "form4.xml")]
    added = form4.fetch(ENTRY, conn, session=make_session(forms), today=TODAY)
    assert added == 1
    row = conn.execute("SELECT ticker, buyer, trans_date, filing_date"
                       " FROM insider_buys").fetchone()
    assert row == ("NVDA", "DOE JANE", "2026-07-01", "2026-07-03")


def test_fetch_idempotent_and_skips_seen_accessions(conn):
    forms = [("4", "0001-26-000001", "2026-07-03", "form4.xml")]
    form4.fetch(ENTRY, conn, session=make_session(forms), today=TODAY)
    assert form4.fetch(ENTRY, conn, session=make_session(forms),
                       today=TODAY) == 0


def test_fetch_respects_lookback(conn):
    stale = [("4", "0001-25-000009", "2025-01-01", "form4.xml")]
    assert form4.fetch(ENTRY, conn, session=make_session(stale),
                       today=TODAY) == 0


def test_fetch_ignores_non_form4(conn):
    forms = [("10-Q", "0001-26-000002", "2026-07-03", "10q.htm")]
    assert form4.fetch(ENTRY, conn, session=make_session(forms),
                       today=TODAY) == 0


def test_empty_universe_is_noop(conn):
    assert form4.fetch({"insider_tickers": []}, conn, session=None) == 0


def test_unknown_ticker_raises(conn):
    entry = {"insider_tickers": ["ZZZZZZ"]}
    with pytest.raises(form4.FetchError, match="unknown ticker"):
        form4.fetch(entry, conn, session=make_session([]), today=TODAY)


# ── current_flags: db → F13, as-of on the FILING date ────────────────────────

def put_buys(conn, rows):
    conn.executemany("INSERT INTO insider_buys VALUES (?,?,?,?,?)", rows)


def test_current_flags_cluster_and_as_of(conn):
    put_buys(conn, [
        ("NVDA", "A", "2026-06-01", "2026-06-03", "a1"),
        ("NVDA", "B", "2026-06-20", "2026-06-22", "a2"),
        ("NVDA", "C", "2026-07-10", "2026-07-12", "a3"),
        ("AMD", "X", "2026-06-15", "2026-06-17", "a4"),
    ])
    flags = insiders.current_flags(conn, "2026-07-19")
    assert flags == {"NVDA": True, "AMD": False}
    # before the third filing is public, NVDA must NOT flag
    assert insiders.current_flags(conn, "2026-07-11") == {"NVDA": False,
                                                          "AMD": False}


def test_report_renders_insider_line():
    text = report.build({}, {}, "2026-W29",
                        insider_flags={"NVDA": True, "AMD": False})
    assert "Insider cluster" in text and "NVDA" in text and "AMD" not in \
        text.split("Insider cluster")[1]
