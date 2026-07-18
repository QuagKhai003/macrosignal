"""EDGAR whale fetcher tests — canned filings, structure from the real ones.

@context  Batch 3.4: context-deduped tag totals (the 397.4B arithmetic), skip-
          if-stored parsimony, 13F totals, decider date, panel assembly.
@done     Those cases + live integration (real Berkshire Q1 2026 numbers).
@todo     —
@limits   Default offline; the canned instance mirrors the probed layout.
@affects  src/fetchers/edgar.py.
"""

import pytest

from src import db, registry
from src.fetchers import edgar

ENTRY = {
    "series_id": "whale_berkshire", "source": "EDGAR",
    "source_url": "https://example.com", "schedule": "quarterly",
    "window": "rolling20y", "pub_lag_days": 0, "cik": "0001067983",
    "cash_tags": ["us-gaap:CashAndCashEquivalentsAtCarryingValue",
                  "brka:USTreasuryBills"],
    "equity_tags": ["us-gaap:EquitySecuritiesFvNi"],
}


def ctx(cid, instant, members=()):
    m = "".join(f"<xbrldi:explicitMember>{x}</xbrldi:explicitMember>"
                for x in members)
    return (f'<context id="{cid}"><entity><segment>{m}</segment></entity>'
            f"<period><instant>{instant}</instant></period></context>")


def fact(tag, cid, value):
    ns, local = tag.split(":")
    return (f'<{ns}:{local} contextRef="{cid}" unitRef="usd"'
            f' decimals="-6">{value}</{ns}:{local}>')


INSTANCE = "".join([
    "<xbrl>",
    ctx("c_ins", "2026-03-31", ["brka:InsuranceAndOtherMember"]),
    ctx("c_rail", "2026-03-31", ["brka:RailroadUtilitiesAndEnergyMember"]),
    ctx("c_prior", "2025-12-31", ["brka:InsuranceAndOtherMember"]),
    fact("us-gaap:CashAndCashEquivalentsAtCarryingValue", "c_ins", 51500000000),
    fact("us-gaap:CashAndCashEquivalentsAtCarryingValue", "c_rail", 6600000000),
    fact("us-gaap:CashAndCashEquivalentsAtCarryingValue", "c_prior", 44000000000),
    fact("brka:USTreasuryBills", "c_ins", 339300000000),
    fact("us-gaap:EquitySecuritiesFvNi", "c_ins", 288000000000),
    "</xbrl>",
])

SUBMISSIONS = {"filings": {"recent": {
    "form": ["13F-HR", "10-Q", "8-K"],
    "accessionNumber": ["0001-26-000002", "0001-26-000001", "0001-26-000003"],
    "filingDate": ["2026-05-15", "2026-05-04", "2026-05-01"],
    "primaryDocument": ["xslForm13F_X02/primary_doc.xml",
                        "brka-20260331.htm", "d8k.htm"],
}}}

INFOTABLE = ("<informationTable><infoTable><value>200000000000</value>"
             "</infoTable><infoTable><value>88000000000</value></infoTable>"
             "</informationTable>")


class FakeResponse:
    def __init__(self, payload=None, text="", status=200):
        self._payload, self.text, self.status_code = payload, text, status

    def json(self):
        return self._payload


class FakeSession:
    def get(self, url, headers, timeout):
        if "submissions" in url:
            return FakeResponse(payload=SUBMISSIONS)
        if url.endswith("index.json"):
            return FakeResponse(payload={"directory": {"item": [
                {"name": "form13fInfoTable.xml"}, {"name": "primary_doc.xml"}]}})
        if "infotable" in url.lower():
            return FakeResponse(text=INFOTABLE)
        if url.endswith("_htm.xml"):
            return FakeResponse(text=INSTANCE)
        return FakeResponse(status=404)


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def test_cash_arithmetic_matches_the_worked_example(conn):
    edgar.fetch(ENTRY, conn, session=FakeSession())
    cash = conn.execute("SELECT data_date, pub_date, value FROM observations"
                        " WHERE series_id = 'whale_brk_cash'").fetchone()
    # 51.5 + 6.6 + 339.3 = 397.4B; prior-period contexts ignored
    assert cash == ("2026-03-31", "2026-05-04", pytest.approx(397.4e9))
    frac = conn.execute("SELECT value FROM observations WHERE series_id ="
                        " 'whale_brk_cash_fraction'").fetchone()[0]
    assert frac == pytest.approx(397.4 / (397.4 + 288.0), abs=1e-6)


def test_13f_total_summed(conn):
    edgar.fetch(ENTRY, conn, session=FakeSession())
    row = conn.execute("SELECT data_date, value FROM observations WHERE"
                       " series_id = 'whale_brk_13f_total'").fetchone()
    assert row == ("2026-03-31", pytest.approx(288e9))  # 200 + 88


def test_second_fetch_skips_stored_periods(conn):
    edgar.fetch(ENTRY, conn, session=FakeSession())
    assert edgar.fetch(ENTRY, conn, session=FakeSession()) == 0


def test_no_dimension_total_preferred():
    instance = "".join([
        "<xbrl>", ctx("c_total", "2026-03-31"),
        ctx("c_seg", "2026-03-31", ["brka:InsuranceAndOtherMember"]),
        fact("brka:USTreasuryBills", "c_total", 340000000000),
        fact("brka:USTreasuryBills", "c_seg", 339300000000), "</xbrl>"])
    total = edgar._tag_total(instance, ["brka:USTreasuryBills"], "2026-03-31")
    assert total == pytest.approx(340e9)  # consolidated wins, no double count


def test_quarter_end_before():
    assert edgar._quarter_end_before("2026-05-15") == "2026-03-31"
    assert edgar._quarter_end_before("2026-02-17") == "2025-12-31"


def test_panel_and_decider(conn):
    edgar.fetch(ENTRY, conn, session=FakeSession())
    panel = edgar.panel_data(conn, "2026-07-18")
    assert panel["cash"] == pytest.approx(397.4e9)
    assert panel["decider"] == "2026-08-03"  # filed 05-04 + 91 days
    assert edgar.panel_data(conn, "2026-05-01") is None  # before the filing


@pytest.mark.integration
def test_live_berkshire_q1_2026(conn):
    entry = next(e for e in registry.load_registry()
                 if e["series_id"] == "whale_berkshire")
    edgar.fetch(entry, conn, session=None)
    panel = edgar.panel_data(conn, "2026-07-18")
    assert abs(panel["cash"] - 397.4e9) < 2e9   # the doc's worked example
    assert 0.5 < panel["fraction"] < 0.65        # "~60% area"
    assert panel["decider"].startswith("2026-08")  # "due August"
