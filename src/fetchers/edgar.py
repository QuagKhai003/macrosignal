"""EDGAR whale fetcher — filings only, never headlines (§5c Rule 1).

@context  The whale ledger's data arm: Berkshire's cash position from 10-Q/
          10-K XBRL instances (companyfacts hides segment-dimensioned facts,
          so we parse the raw instance), plus the latest 13F-HR total. Every
          number traces to a signed filing; pub_date = the EDGAR filing date
          (true as-of, no lag guessing).
@done     fetch(): submissions index -> up to MAX_BACKFILL latest 10-Q/10-K,
          skipping period-ends already stored (6MB instances parsed once,
          ever); per-tag context-deduped extraction (prefer a no-dimension
          total, else sum one value per segment context); stores
          whale_brk_cash, whale_brk_equities, whale_brk_cash_fraction,
          whale_brk_13f_total. next_10q_due() decider helper.
@todo     13F top-holdings display (total stored; names deferred — logged);
          more whales = more registry entries.
@limits   Declared User-Agent per EDGAR policy. Tag names are config
          (signals.yaml) — tag drift before ~2022 limits deep backfill.
          Raises FetchError loud; orchestrator journals it.
@affects  weekly_run; the report's whale panel (3.4) + divergence banner.
"""

import datetime as dt
import json
import re
import sqlite3

import requests

from src.fetchers import base, edgarevents, form4

_HEADERS = {"User-Agent": "macrosignal personal research quangngokhai@gmail.com"}
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc}"
MAX_BACKFILL = 8  # newest filings parsed on first run; archive accrues weekly
FILING_LAG_DAYS = 91  # decider: next 10-Q due ~= last filed + one quarter


class FetchError(RuntimeError):
    pass


def fetch(entry: dict, conn: sqlite3.Connection, session=None) -> int:
    session = session or requests.Session()
    cik = entry["cik"]
    subs = _get_json(session, SUBMISSIONS_URL.format(cik=cik))
    recent = subs["filings"]["recent"]
    filings = list(zip(recent["form"], recent["accessionNumber"],
                       recent["filingDate"], recent["primaryDocument"]))

    for sid in ("whale_brk_cash", "whale_brk_equities",
                "whale_brk_cash_fraction", "whale_brk_13f_total"):
        base.ensure_series_row(conn, sid, entry,
                               f"component of {entry['series_id']}")
    added = 0
    added += _fetch_quarterlies(entry, conn, session, cik, filings)
    added += _fetch_13f_total(entry, conn, session, cik, filings)
    conn.commit()
    # the equity universe rides this entry's config; both no-op while empty
    added += form4.fetch(entry, conn, session=session)
    added += edgarevents.fetch(entry, conn, session=session)
    return added


def panel_data(conn: sqlite3.Connection, as_of: str) -> dict | None:
    """The whale panel's numbers, as-of. None until a filing is stored."""
    def latest(sid, n=2):
        return conn.execute(
            "SELECT data_date, value FROM observations WHERE series_id = ?"
            " AND pub_date <= ? ORDER BY data_date DESC LIMIT ?",
            (sid, as_of, n)).fetchall()

    cash = latest("whale_brk_cash", 1)
    if not cash:
        return None
    fractions = latest("whale_brk_cash_fraction", 2)
    t13 = latest("whale_brk_13f_total", 1)
    return {
        "period": cash[0][0],
        "cash": cash[0][1],
        "fraction": fractions[0][1] if fractions else None,
        "prior_fraction": fractions[1][1] if len(fractions) > 1 else None,
        "thirteenf_total": t13[0][1] if t13 else None,
        "decider": next_10q_due(conn),
    }


def next_10q_due(conn: sqlite3.Connection) -> str | None:
    """Decider date (§5c Rule 3): latest filing pub_date + ~one quarter."""
    row = conn.execute(
        "SELECT MAX(pub_date) FROM observations WHERE series_id ="
        " 'whale_brk_cash'").fetchone()
    if not row or not row[0]:
        return None
    filed = dt.date.fromisoformat(row[0])
    return (filed + dt.timedelta(days=FILING_LAG_DAYS)).isoformat()


def _fetch_quarterlies(entry, conn, session, cik, filings) -> int:
    stored = {r[0] for r in conn.execute(
        "SELECT data_date FROM observations WHERE series_id ="
        " 'whale_brk_cash'")}
    added = 0
    quarterlies = [f for f in filings if f[0] in ("10-Q", "10-K")][:MAX_BACKFILL]
    for _form, acc, filed, primary in quarterlies:
        period_end = _period_from_primary(primary)
        if period_end is None or period_end in stored:
            continue
        instance = _instance_xml(session, cik, acc, primary)
        cash = _tag_total(instance, entry["cash_tags"], period_end)
        equities = _tag_total(instance, entry["equity_tags"], period_end)
        if cash is None:
            raise FetchError(f"EDGAR {acc}: no cash facts at {period_end}")
        rows = [("whale_brk_cash", cash)]
        if equities:
            rows.append(("whale_brk_equities", equities))
            rows.append(("whale_brk_cash_fraction", cash / (cash + equities)))
        for sid, value in rows:
            added += base.insert_observations(conn, sid,
                                              [(period_end, filed, value)])
    return added


def _fetch_13f_total(entry, conn, session, cik, filings) -> int:
    f13 = [f for f in filings if f[0] == "13F-HR"]
    if not f13:
        return 0
    _form, acc, filed, _primary = f13[0]
    period_end = _quarter_end_before(filed)
    exists = conn.execute(
        "SELECT 1 FROM observations WHERE series_id = 'whale_brk_13f_total'"
        " AND data_date = ?", (period_end,)).fetchone()
    if exists:
        return 0
    acc_nodash = acc.replace("-", "")
    index = _get_json(session,
                      ARCHIVE_URL.format(cik_int=int(cik), acc=acc_nodash)
                      + "/index.json")
    names = [i["name"] for i in index["directory"]["item"]]
    # the info table is the .xml that is NOT primary_doc (its name is often
    # an arbitrary number, e.g. 53405.xml)
    table = next((n for n in names if n.lower().endswith(".xml")
                  and "primary_doc" not in n.lower()), None)
    if table is None:
        raise FetchError(f"EDGAR 13F {acc}: no infotable xml")
    xml = _get_text(session, ARCHIVE_URL.format(cik_int=int(cik),
                                                acc=acc_nodash) + f"/{table}")
    values = [float(v) for v in re.findall(r"<value>(\d+)</value>", xml)]
    if not values:
        raise FetchError(f"EDGAR 13F {acc}: no value fields")
    return base.insert_observations(conn, "whale_brk_13f_total",
                                    [(period_end, filed, sum(values))])


def _tag_total(instance: str, tags: list[str], period_end: str) -> float | None:
    """Per tag: prefer a no-dimension consolidated fact; else sum one value
    per distinct segment context. Sum across tags."""
    contexts = _contexts(instance)
    total, found = 0.0, False
    for tag in tags:
        ns, local = tag.split(":")
        by_ctx: dict[str, float] = {}
        for m in re.finditer(
                rf'<{ns}:{local}[^>]*contextRef="([^"]+)"[^>]*>([\d.]+)<', instance):
            cid, val = m.group(1), float(m.group(2))
            end, members = contexts.get(cid, (None, []))
            if end == period_end:
                by_ctx[cid] = (val, members)
        if not by_ctx:
            continue
        found = True
        no_dim = [v for v, mem in by_ctx.values() if not mem]
        if no_dim:
            total += max(no_dim)
        else:
            total += sum(v for v, _mem in by_ctx.values())
    return total if found else None


def _contexts(instance: str) -> dict:
    out = {}
    for m in re.finditer(r'<context id="([^"]+)">(.*?)</context>', instance, re.S):
        cid, body = m.groups()
        end = re.search(r"<endDate>([^<]+)<|<instant>([^<]+)<", body)
        members = re.findall(r'>([A-Za-z0-9:_-]+Member)<', body)
        out[cid] = ((end.group(1) or end.group(2)) if end else None, members)
    return out


def _period_from_primary(primary: str) -> str | None:
    m = re.search(r"(\d{8})", primary)  # e.g. brka-20260331.htm
    if not m:
        return None
    s = m.group(1)
    return f"{s[:4]}-{s[4:6]}-{s[6:]}"


def _quarter_end_before(filed: str) -> str:
    d = dt.date.fromisoformat(filed)
    quarter_ends = [dt.date(d.year - 1, 12, 31), dt.date(d.year, 3, 31),
                    dt.date(d.year, 6, 30), dt.date(d.year, 9, 30)]
    return max(q for q in quarter_ends if q < d).isoformat()


def _instance_xml(session, cik, acc, primary) -> str:
    acc_nodash = acc.replace("-", "")
    stem = primary.replace(".htm", "_htm.xml")
    return _get_text(session, ARCHIVE_URL.format(cik_int=int(cik),
                                                 acc=acc_nodash) + f"/{stem}")


def _get_json(session, url):
    resp = session.get(url, headers=_HEADERS, timeout=120)
    if resp.status_code != 200:
        raise FetchError(f"EDGAR {url}: HTTP {resp.status_code}")
    try:
        return resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise FetchError(f"EDGAR {url}: bad json") from exc


def _get_text(session, url) -> str:
    resp = session.get(url, headers=_HEADERS, timeout=120)
    if resp.status_code != 200:
        raise FetchError(f"EDGAR {url}: HTTP {resp.status_code}")
    return resp.text
