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
          per-infoTable extraction of (issuer, value); total = sum of values;
          largest position = the "Best Idea" (research R2) stored in
          whale_top_holding. ledger() + best_ideas() readers for the report.
          Reuses edgar.py plumbing (headers, quarter-end, json/text getters).
@todo     Best Idea vs MARKET weight (needs benchmark caps — we use portfolio
          weight, the practitioner proxy); percentile vs own 20-yr history.
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

import html
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
            have_total = conn.execute(
                "SELECT 1 FROM observations WHERE series_id = ? AND"
                " data_date = ?", (sid, period_end)).fetchone()
            have_top = conn.execute(
                "SELECT 1 FROM whale_top_holding WHERE name = ? AND"
                " period = ?", (name, period_end)).fetchone()
            if have_total and have_top:
                continue  # both pieces stored — skip the download
            holdings = _accession_holdings(session, cik, acc)
            if holdings is None:
                continue  # variant layout — skipped, never guessed
            raw_sum = sum(v for _i, v in holdings)
            # Units are a mess in the wild: thousands before 2023, dollars
            # after — except filers (Baupost, Duquesne) who kept thousands.
            # Deterministic disambiguation: 13F filers must hold >= $100M,
            # so a sub-$100M dollars-reading can only mean thousands.
            total = raw_sum * 1000 if raw_sum < 1e8 else raw_sum
            if not have_total:
                added += base.insert_observations(
                    conn, sid, [(period_end, filed, total)])
            # Best Idea (research R2): the largest single position's weight —
            # the manager's highest-conviction bet. Weight is a ratio, so the
            # unit scaling cancels; issuer name kept as text.
            top_issuer, top_value = max(holdings, key=lambda h: h[1])
            weight = top_value / raw_sum if raw_sum else 0.0
            conn.execute(
                "INSERT OR IGNORE INTO whale_top_holding VALUES (?,?,?,?,?)",
                (name, period_end, top_issuer, weight, filed))
    conn.commit()
    return added


def _accession_holdings(session, cik: str, acc: str):
    """[(issuer, value)] per 13F position, or None on a variant layout.
    Namespace-agnostic per-infoTable extraction: pair each block's issuer
    name with its market value."""
    acc_nodash = acc.replace("-", "")
    url = edgar.ARCHIVE_URL.format(cik_int=int(cik), acc=acc_nodash)
    index = edgar._get_json(session, url + "/index.json")
    names = [i["name"] for i in index["directory"]["item"]]
    table = next((n for n in names if n.lower().endswith(".xml")
                  and "primary_doc" not in n.lower()), None)
    if table is None:
        return None
    xml = edgar._get_text(session, url + f"/{table}")
    out = []
    for block in re.split(r"(?i)<(?:\w+:)?infoTable>", xml)[1:]:
        issuer = re.search(r"(?i)<(?:\w+:)?nameOfIssuer>\s*(.*?)\s*</", block)
        value = re.search(r"(?i)<(?:\w+:)?value>\s*(\d+)\s*</", block)
        if issuer and value:
            out.append((html.unescape(issuer.group(1).strip()),
                        float(value.group(1))))
    if out:
        return out
    # some filers omit the infoTable wrapper split cleanly — fall back to the
    # flat value list (total still works; top holding unavailable → skip)
    values = [float(v) for v in
              re.findall(r"<(?:\w+:)?value>\s*(\d+)\s*</", xml)]
    return [("(unknown)", v) for v in values] if values else None


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


def best_ideas(conn: sqlite3.Connection, as_of: str, whales: dict) -> list[dict]:
    """Per whale, their latest 'Best Idea' as-of (research R2): the single
    largest 13F position and its portfolio weight — the highest-conviction
    bet. Context for the world picture; nothing acts on it."""
    out = []
    for name in whales:
        row = conn.execute(
            "SELECT period, issuer, weight FROM whale_top_holding"
            " WHERE name = ? AND filing_date <= ? ORDER BY period DESC"
            " LIMIT 1", (name, as_of)).fetchone()
        if row:
            out.append({"name": name, "period": row[0], "issuer": row[1],
                        "weight": row[2]})
    return out
