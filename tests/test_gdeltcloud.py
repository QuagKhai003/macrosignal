"""GDELT Cloud fallback tests.

@context  The keyed fallback must fill headlines (scale-free F7 input) and
          NEVER volume rows (scale mismatch would corrupt F6); it fires only
          when the Project route fails, and both-failing raises loud.
@done     Those cases + pagination + plain-text query conversion + missing
          key; live smoke marked integration (single page).
@todo     —
@limits   Default offline.
@affects  src/fetchers/gdeltcloud.py, src/fetchers/gdelt.py.
"""

import datetime as dt

import pytest

from src import db
from src.fetchers import gdelt, gdeltcloud

ENTRY = {
    "series_id": "news_heat", "source": "GDELT",
    "source_url": "https://example.com", "schedule": "weekly",
    "window": "fixed_threshold", "pub_lag_days": 0,
    "themes": {"wti": '"oil price" OR "crude oil"'},
    "cloud_fallback": {"url": "https://cloud.example/v2/stories",
                       "key_env": "GDLTE_CLOUD_API_KEY"},
}
TODAY = dt.date(2026, 7, 18)

CLOUD_PAGE = {
    "success": True,
    "data": [
        {"title": "Oil rallies as strait closes", "story_date": "2026-07-14",
         "top_articles": [{"url": "https://x/a", "rank": 1}]},
        {"title": "", "story_date": "2026-07-14"},              # empty: skip
        {"title": "Crude fears grip markets", "story_date": "2026-07-15",
         "top_articles": []},
    ],
    "pagination": {"next_cursor": None},
}


class CloudSession:
    def __init__(self, status=200):
        self.calls, self._status = [], status

    def get(self, url, params, timeout, headers):
        self.calls.append((url, dict(params), headers))

        class R:
            status_code = self._status
            def json(self):
                return CLOUD_PAGE
        return R()


class Down:
    def get(self, *a, **k):
        raise ConnectionError("project down")


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("GDLTE_CLOUD_API_KEY", "gdelt_sk_test")
    conn = db.connect(tmp_path / "t.db")
    db.init_db(conn)
    yield conn
    conn.close()


def test_plain_text_conversion():
    assert gdeltcloud._plain_text('"gold price" OR "gold rally"') == \
        "gold price gold rally"


def test_cloud_fills_headlines_only(conn):
    session = CloudSession()
    added = gdeltcloud.fetch_theme_headlines(
        ENTRY, conn, "wti", TODAY - dt.timedelta(days=7), TODAY,
        session=session)
    assert added == 2
    assert conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0
    url, params, headers = session.calls[0]
    assert headers["Authorization"] == "Bearer gdelt_sk_test"
    assert params["search"] == "oil price crude oil"
    assert params["languages"] == "en"


def test_missing_key_raises(conn, monkeypatch):
    monkeypatch.delenv("GDLTE_CLOUD_API_KEY")
    from src import config
    monkeypatch.setattr(config, "ENV_PATH", __import__("pathlib").Path("absent"))
    with pytest.raises(gdeltcloud.FetchError, match="GDLTE_CLOUD_API_KEY"):
        gdeltcloud.fetch_theme_headlines(ENTRY, conn, "wti",
                                         TODAY - dt.timedelta(days=7), TODAY,
                                         session=CloudSession())


def test_project_failure_triggers_cloud_fallback(conn, monkeypatch):
    cloud = CloudSession()
    monkeypatch.setattr(gdeltcloud, "fetch_theme_headlines",
                        lambda *a, **k: 2)
    added = gdelt.fetch(ENTRY, conn, session=Down(), today=TODAY,
                        pause=lambda s: None)
    assert added == 2
    flag = conn.execute("SELECT detail FROM journal WHERE event_type ="
                        " 'flag'").fetchone()[0]
    assert "news fallback: gdeltcloud" in flag and "volume skipped" in flag
    assert conn.execute("SELECT COUNT(*) FROM observations WHERE series_id"
                        " LIKE 'news_vol%'").fetchone()[0] == 0


def test_both_routes_failing_raises(conn, monkeypatch):
    def boom(*a, **k):
        raise gdeltcloud.FetchError("cloud down too")
    monkeypatch.setattr(gdeltcloud, "fetch_theme_headlines", boom)
    with pytest.raises(gdelt.FetchError, match="both failed"):
        gdelt.fetch(ENTRY, conn, session=Down(), today=TODAY,
                    pause=lambda s: None)


@pytest.mark.integration
@pytest.mark.skipif(not __import__("src.config", fromlist=["config"]).get_key(
    "GDLTE_CLOUD_API_KEY"), reason="no cloud key")
def test_live_cloud_single_page(conn):
    from src import registry
    entry = next(e for e in registry.load_registry()
                 if e["series_id"] == "news_heat")
    added = gdeltcloud.fetch_theme_headlines(
        entry, conn, "wti", dt.date.today() - dt.timedelta(days=7),
        dt.date.today())
    assert added >= 0  # reachable + parseable is the point
