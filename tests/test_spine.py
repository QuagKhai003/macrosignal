"""Spine derivation tests — hand-computed net liquidity + readouts.

@context  Batch 1.5: the derived series and readouts are where unit mistakes
          and look-ahead leaks would hide; both are pinned here by hand.
@done     Unit normalization ($mn/$mn/$bn -> $bn), component alignment
          (latest at-or-before), as-of pub_date filtering, idempotency,
          weekly-resample correlation plumbing, summarize shape.
@todo     —
@limits   Offline, deterministic.
@affects  src/spine.py.
"""

import pytest

from src import db, spine


@pytest.fixture()
def conn(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    conn.execute("INSERT INTO series VALUES"
                 " ('net_liquidity', 'FRED', 'u', 'weekly', 'rolling10y', '')")
    for sid in ("fred_walcl", "fred_wtregen", "fred_rrpontsyd"):
        conn.execute("INSERT INTO series VALUES (?, 'FRED', 'u', 'weekly',"
                     " 'rolling10y', '')", (sid,))
    yield conn
    conn.close()


def obs(conn, sid, rows):
    conn.executemany("INSERT INTO observations VALUES (?, ?, ?, ?)",
                     [(sid, d, p, v) for d, p, v in rows])


def test_net_liquidity_units_and_alignment(conn):
    # WALCL 6,743,028 $mn; TGA 756,218 $mn (dated 07-14, latest <= 07-15);
    # RRP 0.1 $bn -> 6743.028 - 756.218 - 0.1 = 5986.71 $bn
    obs(conn, "fred_walcl", [("2026-07-15", "2026-07-16", 6743028.0)])
    obs(conn, "fred_wtregen", [("2026-07-14", "2026-07-15", 756218.0)])
    obs(conn, "fred_rrpontsyd", [("2026-07-15", "2026-07-16", 0.1)])
    assert spine.derive_net_liquidity(conn, "2026-07-18") == 1
    d, p, v = conn.execute("SELECT data_date, pub_date, value FROM observations"
                           " WHERE series_id = 'net_liquidity'").fetchone()
    assert (d, p) == ("2026-07-15", "2026-07-16")  # pub = max(component pubs)
    assert v == pytest.approx(5986.71)


def test_net_liquidity_respects_as_of(conn):
    # component published AFTER as_of must be invisible -> no row derivable
    obs(conn, "fred_walcl", [("2026-07-15", "2026-07-16", 6743028.0)])
    obs(conn, "fred_wtregen", [("2026-07-14", "2026-07-20", 756218.0)])
    obs(conn, "fred_rrpontsyd", [("2026-07-15", "2026-07-16", 0.1)])
    assert spine.derive_net_liquidity(conn, "2026-07-18") == 0


def test_net_liquidity_skips_dates_without_components(conn):
    obs(conn, "fred_walcl", [("2026-07-08", "2026-07-09", 6700000.0),
                             ("2026-07-15", "2026-07-16", 6743028.0)])
    obs(conn, "fred_wtregen", [("2026-07-10", "2026-07-11", 756218.0)])
    obs(conn, "fred_rrpontsyd", [("2026-07-10", "2026-07-11", 0.1)])
    # 07-08 has no component at-or-before it; only 07-15 derives
    assert spine.derive_net_liquidity(conn, "2026-07-18") == 1


def test_net_liquidity_idempotent(conn):
    obs(conn, "fred_walcl", [("2026-07-15", "2026-07-16", 6743028.0)])
    obs(conn, "fred_wtregen", [("2026-07-14", "2026-07-15", 756218.0)])
    obs(conn, "fred_rrpontsyd", [("2026-07-15", "2026-07-16", 0.1)])
    spine.derive_net_liquidity(conn, "2026-07-18")
    assert spine.derive_net_liquidity(conn, "2026-07-18") == 0


def test_summarize_reports_insufficient_on_thin_data(conn):
    obs(conn, "fred_walcl", [("2026-07-15", "2026-07-16", 6743028.0)])
    summary = spine.summarize(conn, "2026-07-18")
    assert summary["cot_gold_party_pct"] is None
    assert summary["net_liquidity_pct"] is None
    assert summary["price_gold_momentum"] is None
    assert summary["gold_realyield_corr_52w"] is None


def test_market_valuation_forward_fills_published_gdp(conn):
    conn.execute("INSERT INTO series VALUES ('price_wilshire', 'Y', 'u',"
                 " 'daily', 'sma200', '')")
    conn.execute("INSERT INTO series VALUES ('fred_gdp', 'FRED', 'u',"
                 " 'quarterly', 'rolling20y', '')")
    conn.execute("INSERT INTO series VALUES ('market_valuation', 'FRED', 'u',"
                 " 'weekly', 'rolling20y', '')")
    # GDP Q1 (data 01-01) published 05-01; Q2 (04-01) published 08-01
    obs(conn, "fred_gdp", [("2026-01-01", "2026-05-01", 30000.0),
                           ("2026-04-01", "2026-08-01", 32000.0)])
    # weekly closes in June: only Q1's GDP is PUBLISHED by then (as-of!)
    obs(conn, "price_wilshire", [("2026-06-05", "2026-06-05", 75000.0),
                                 ("2026-06-12", "2026-06-12", 76000.0)])
    assert spine.derive_market_valuation(conn, "2026-07-18") == 2
    rows = conn.execute(
        "SELECT data_date, value FROM observations WHERE series_id ="
        " 'market_valuation' ORDER BY data_date").fetchall()
    assert rows[0] == ("2026-06-05", pytest.approx(75000.0 / 30000.0))
    assert rows[1] == ("2026-06-12", pytest.approx(76000.0 / 30000.0))
    # idempotent
    assert spine.derive_market_valuation(conn, "2026-07-18") == 0


def test_market_valuation_no_gdp_published_yet(conn):
    conn.execute("INSERT INTO series VALUES ('price_wilshire', 'Y', 'u',"
                 " 'daily', 'sma200', '')")
    conn.execute("INSERT INTO series VALUES ('fred_gdp', 'FRED', 'u',"
                 " 'quarterly', 'rolling20y', '')")
    conn.execute("INSERT INTO series VALUES ('market_valuation', 'FRED', 'u',"
                 " 'weekly', 'rolling20y', '')")
    obs(conn, "fred_gdp", [("2026-01-01", "2026-05-01", 30000.0)])
    obs(conn, "price_wilshire", [("2026-02-06", "2026-02-06", 75000.0)])
    # the only GDP row publishes 05-01, AFTER the close: nothing derivable
    assert spine.derive_market_valuation(conn, "2026-03-01") == 0


def test_summarize_party_pct_hand_check(conn):
    import datetime as dt
    conn.execute("INSERT INTO series VALUES"
                 " ('cot_gold', 'CFTC', 'u', 'weekly', 'rolling3y', '')")
    # 130 weekly obs (>= 0.8*156): values 1..130 rising, latest is the max
    # -> pct = 100*(129 + 0.5)/130
    start = dt.date(2024, 1, 2)
    obs(conn, "cot_gold",
        [((start + dt.timedelta(weeks=i)).isoformat(),
          (start + dt.timedelta(weeks=i, days=3)).isoformat(), float(i + 1))
         for i in range(130)])
    summary = spine.summarize(conn, "2026-07-18")
    assert summary["cot_gold_party_pct"] == pytest.approx(100 * 129.5 / 130)
