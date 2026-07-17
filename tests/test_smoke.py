"""End-to-end runner tests (offline) — orchestration, not sources.

@context  Batch 1.5 acceptance: the weekly run never crashes on a source
          failure (journal flag + continue), always writes its run row, mirrors
          the registry, derives net liquidity, prints readouts.
@done     All-sources-down run; partial run (FRED up, others down) deriving
          net liquidity end-to-end; package import.
@todo     —
@limits   Offline via injected sessions; real signals.yaml.
@affects  weekly_run.py, src/spine.py, src/fetchers/*.
"""

import datetime as dt

import weekly_run
from src import db

AS_OF = dt.date(2026, 7, 18)


class DownSession:
    def get(self, *args, **kwargs):
        raise ConnectionError("source is down")


class FredOkSession:
    PAYLOADS = {
        "WALCL": [("2026-07-08", "6700000"), ("2026-07-15", "6743028")],
        "WTREGEN": [("2026-07-08", "740000"), ("2026-07-15", "756218")],
        "RRPONTSYD": [("2026-07-08", "0.2"), ("2026-07-15", "0.1")],
    }

    def get(self, url, params, timeout):
        rows = self.PAYLOADS.get(params["series_id"], [])
        payload = {"observations": [{"date": d, "value": v} for d, v in rows]}

        class R:
            status_code = 200
            def json(self, _p=payload):
                return _p
        return R()


def test_all_sources_down_still_completes(tmp_path, capsys):
    db_path = tmp_path / "signals.db"
    sessions = {"FRED": DownSession(), "CFTC": DownSession(),
                "Yahoo": DownSession(), "EIA": DownSession()}
    assert weekly_run.main(db_path=db_path, today=AS_OF,
                           sessions=sessions) == 0
    assert capsys.readouterr().out.strip().endswith("run complete")

    conn = db.connect(db_path)
    flags = conn.execute("SELECT COUNT(*) FROM journal"
                         " WHERE event_type = 'flag'").fetchone()[0]
    assert flags == 6  # 3 FRED entries + CFTC + Yahoo + EIA, all down
    run_detail = conn.execute("SELECT detail FROM journal"
                              " WHERE event_type = 'run'").fetchone()[0]
    assert "6 fetch failures" in run_detail
    assert conn.execute("SELECT COUNT(*) FROM series").fetchone()[0] == 6
    conn.close()


def test_partial_run_derives_net_liquidity(tmp_path, capsys):
    db_path = tmp_path / "signals.db"
    sessions = {"FRED": FredOkSession(), "CFTC": DownSession(),
                "Yahoo": DownSession(), "EIA": DownSession()}
    assert weekly_run.main(db_path=db_path, today=AS_OF,
                           sessions=sessions) == 0
    out = capsys.readouterr().out
    assert "net_liquidity_pct: insufficient" in out  # 2 obs << 0.8*520

    conn = db.connect(db_path)
    rows = conn.execute(
        "SELECT data_date, value FROM observations"
        " WHERE series_id = 'net_liquidity' ORDER BY data_date").fetchall()
    # hand-check: 6743.028 - 756.218 - 0.1 = 5986.71 on 07-15
    assert len(rows) == 2
    assert rows[1][0] == "2026-07-15"
    assert abs(rows[1][1] - 5986.71) < 0.001
    run_detail = conn.execute("SELECT detail FROM journal"
                              " WHERE event_type = 'run'").fetchone()[0]
    assert "3 fetch failures" in run_detail
    conn.close()


def test_src_package_imports():
    import src  # noqa: F401
