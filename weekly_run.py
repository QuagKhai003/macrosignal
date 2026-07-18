"""Weekly batch entry point.

@context  The one command the user runs every Saturday (after Friday's CFTC
          release). Phase 1 shape: validate registry -> fetch all spine
          sources -> derive net liquidity -> print the readouts -> journal.
@done     1.5: fetcher dispatch by registry `source` (FRED/CFTC/Yahoo), a
          failed fetcher writes a journal `flag` row and the run continues
          (never crashes); net-liquidity derivation; readout printout;
          one `run` journal row with the new-observation count.
@todo     Phase 2: state machine + changes-first plain-text report replace
          the raw readout printout.
@limits   Network only inside the fetchers; `sessions` injection keeps tests
          offline. Every printed number comes from src/formulas.py over
          as-of-dated data (Golden Rule).
@affects  src/db.py, src/registry.py, src/spine.py, src/fetchers/*, signals.db.
"""

import datetime as dt

from src import (alarms, classifier, db, insiders, registry, report, spine,
                 states, weather)
from src.fetchers import (cot, earnings, ecb, edgar, eia, fred, gdelt, imf,
                          nass, prices)

FETCHERS = {"FRED": fred.fetch, "CFTC": cot.fetch, "Yahoo": prices.fetch,
            "EIA": eia.fetch, "EDGAR": edgar.fetch, "GDELT": gdelt.fetch,
            "NASS": nass.fetch, "IMF": imf.fetch, "ECB": ecb.fetch,
            "XBRL": earnings.fetch}


def main(db_path=db.DB_PATH, registry_path=registry.REGISTRY_PATH,
         today: dt.date | None = None, sessions: dict | None = None,
         full: bool = False) -> int:
    today = today or dt.date.today()
    as_of = today.isoformat()
    entries = registry.load_registry(registry_path, as_of=today)
    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        for e in entries:
            conn.execute(
                "INSERT OR REPLACE INTO series VALUES (?, ?, ?, ?, ?, ?)",
                (e["series_id"], e["source"], e["source_url"],
                 e["schedule"], e["window"], e["causal_sentence"]))

        added, failures = 0, 0
        for e in entries:
            fetch = FETCHERS.get(e["source"])
            if fetch is None:
                continue  # manual-entry signals have no fetcher
            try:
                kwargs = {}
                if sessions and e["source"] in sessions:
                    kwargs["session"] = sessions[e["source"]]
                added += fetch(e, conn, **kwargs)
            except Exception as exc:  # any fetcher failure: log, keep running
                failures += 1
                conn.execute(
                    "INSERT INTO journal (date, market_id, event_type, detail,"
                    " price_at_event) VALUES (?, NULL, 'flag', ?, NULL)",
                    (as_of, f"fetch failed {e['series_id']}: {exc}"))

        news_entry = next((e for e in entries
                           if e["series_id"] == "news_heat"), None)
        if news_entry is not None:
            try:  # the caged LLM: a failure means honest "insufficient" news
                classifier.classify_pending(conn, news_entry)
            except Exception as exc:
                conn.execute(
                    "INSERT INTO journal (date, market_id, event_type, detail,"
                    " price_at_event) VALUES (?, NULL, 'flag', ?, NULL)",
                    (as_of, f"classification failed: {exc}"))

        added += spine.derive_net_liquidity(conn, as_of)
        added += spine.derive_market_valuation(conn, as_of)
        added += spine.derive_rate_differential(conn, as_of)
        added += spine.derive_corn_stocks_use(conn, as_of)
        added += spine.derive_market_rate_differential(conn, as_of)
        added += spine.derive_oil_curve_spread(conn, as_of)
        added += spine.derive_semis_earnings(conn, as_of)
        added += spine.derive_semis_valuation(conn, as_of)
        summary = spine.summarize(conn, as_of)
        week = states.iso_week(today)
        light = weather.light(conn, as_of)
        prev_states = states.previous_states(conn, week)
        market_states = states.run_week(conn, today,
                                        weather_light=light["light"])
        summary["weather_points"] = light["points"]
        for name, g in light["gauges"].items():
            summary[f"weather_{name}"] = g["value"]
        whale_panel = edgar.panel_data(conn, as_of)
        divergence = bool(
            light["gauges"]["manager_cash"]["red"] is True and whale_panel
            and whale_panel["fraction"] is not None
            and whale_panel["fraction"] > 0.5)
        budget = alarms.alarm_budget(conn, today)
        summary["alarms_rolling_year"] = budget["events"]
        insider_flags = insiders.current_flags(conn, as_of)
        conn.execute(
            "INSERT INTO journal (date, market_id, event_type, detail,"
            " price_at_event) VALUES (?, NULL, 'run', ?, NULL)",
            (as_of, f"run complete - {added} new observations,"
                    f" {failures} fetch failures"))
        conn.commit()
    finally:
        conn.close()

    print(report.build(market_states, prev_states, week,
                       weather=light["light"], summary=summary,
                       full=full, whale=whale_panel, divergence=divergence,
                       alarm_banner=budget["banner"],
                       insider_flags=insider_flags))
    print("run complete")
    return 0


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")  # report uses em-dashes
    raise SystemExit(main(full="--full" in sys.argv))
