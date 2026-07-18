"""Whale-ledger fetcher — 13F totals for the named whales (actors A1).

@context  The user's core vision (docs/ACTORS.md): watch the people who know
          first. Berkshire's ledger existed; this points the same telescope
          at more legendary filers (Baupost, Appaloosa, Soros, Pershing,
          Duquesne — CIKs verified 2026-07-19, 13F-HR depth 1999+..2012+).
          Quarterly 13F-HR total long US-equity value per whale, pub_date =
          the filing date (true as-of, ~45 days after quarter end). CONTEXT,
          not a graded signal: it feeds the report's whale-ledger lines, the
          falsification criteria are untouched.
@done     fetch(): per whale, newest MAX_BACKFILL 13F-HRs, skip stored
          period-ends (archive accrues weekly, the Berkshire pattern);
          infotable xml = the non-primary .xml in the accession index; total
          = sum of <value> fields. ledger(): per-whale latest vs prior total
          for the report. Reuses edgar.py plumbing (headers, quarter-end,
          json/text getters).
@todo     Per-holding names (parked, same as Berkshire's 13F names);
          percentile vs own 20-yr history once enough quarters accrue.
@limits   13F shows long US stocks only — no shorts, no futures, no cash;
          disclosed ~45 days late. Direction and size of change is the
          honest content. Values are THOUSANDS before 2023 period-ends and
          dollars after (SEC rule change) — normalized to dollars here;
          value tags may be namespaced (regex is namespace-agnostic).
          edgar.py's Berkshire 13F parser shares the plain-tag fragility
          (noted in LOG; BRK's own filings use plain tags today).
@affects  observations under whale_<name>_13f_total; src/report.py whale
          ledger lines; weekly_run (source EDGAR13F).
"""

import re
import sqlite3

import requests

from src.fetchers import base, edgar

MAX_BACKFILL = 8  # newest filings on first run (~2 years); accrues weekly


class FetchError(RuntimeError):
    pass


def fetch(entry: dict, conn: sqlite3.Connection, session=None) -> int:
    session = session or requests.Session()
    added = 0
    for name, cik in entry["whales"].items():
        sid = f"whale_{name}_13f_total"
        subs = edgar._get_json(session,  # fetch BEFORE registering the series
                               edgar.SUBMISSIONS_URL.format(cik=cik))
        base.ensure_series_row(conn, sid, entry, f"13F-HR total, {name}")
        recent = subs["filings"]["recent"]
        filings = [(acc, filed) for form, acc, filed in
                   zip(recent["form"], recent["accessionNumber"],
                       recent["filingDate"])
                   if form == "13F-HR"][:MAX_BACKFILL]
        for acc, filed in filings:
            period_end = edgar._quarter_end_before(filed)
            if conn.execute("SELECT 1 FROM observations WHERE series_id = ?"
                            " AND data_date = ?", (sid, period_end)).fetchone():
                continue
            total = _accession_total(session, cik, acc)
            if total is None:
                continue  # variant layout — skipped, never guessed
            # Units are a mess in the wild: thousands before 2023, dollars
            # after — except filers (Baupost, Duquesne) who kept thousands.
            # Deterministic disambiguation: 13F filers must hold >= $100M,
            # so a sub-$100M dollars-reading can only mean thousands.
            if total < 1e8:
                total *= 1000
            added += base.insert_observations(
                conn, sid, [(period_end, filed, total)])
    conn.commit()
    return added


def _accession_total(session, cik: str, acc: str) -> float | None:
    acc_nodash = acc.replace("-", "")
    url = edgar.ARCHIVE_URL.format(cik_int=int(cik), acc=acc_nodash)
    index = edgar._get_json(session, url + "/index.json")
    names = [i["name"] for i in index["directory"]["item"]]
    table = next((n for n in names if n.lower().endswith(".xml")
                  and "primary_doc" not in n.lower()), None)
    if table is None:
        return None
    xml = edgar._get_text(session, url + f"/{table}")
    # namespace-agnostic: filers write <value> or <ns1:value>, +/- whitespace
    values = [float(v) for v in
              re.findall(r"<(?:\w+:)?value>\s*(\d+)\s*</", xml)]
    return sum(values) if values else None


def ledger(conn: sqlite3.Connection, as_of: str, whales: dict) -> list[dict]:
    """Per whale: latest and prior quarterly totals visible as-of. The
    report renders these; nothing downstream acts on them."""
    out = []
    for name in whales:
        rows = conn.execute(
            "SELECT data_date, value FROM observations WHERE series_id = ?"
            " AND pub_date <= ? ORDER BY data_date DESC LIMIT 2",
            (f"whale_{name}_13f_total", as_of)).fetchall()
        if not rows:
            continue
        out.append({"name": name, "period": rows[0][0], "total": rows[0][1],
                    "prior": rows[1][1] if len(rows) > 1 else None})
    return out
