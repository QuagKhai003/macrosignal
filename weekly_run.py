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

from src import db, registry, spine, states
from src.fetchers import cot, eia, fred, prices

FETCHERS = {"FRED": fred.fetch, "CFTC": cot.fetch, "Yahoo": prices.fetch,
            "EIA": eia.fetch}


def main(db_path=db.DB_PATH, registry_path=registry.REGISTRY_PATH,
         today: dt.date | None = None, sessions: dict | None = None) -> int:
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

        added += spine.derive_net_liquidity(conn, as_of)
        summary = spine.summarize(conn, as_of)
        market_states = states.run_week(conn, today)
        conn.execute(
            "INSERT INTO journal (date, market_id, event_type, detail,"
            " price_at_event) VALUES (?, NULL, 'run', ?, NULL)",
            (as_of, f"run complete - {added} new observations,"
                    f" {failures} fetch failures"))
        conn.commit()
    finally:
        conn.close()

    for market, r in market_states.items():
        print(f"{market}: {r['state']} (age {r['age_weeks']}w,"
              f" size {r['size_fraction']:.4f})")
    for key, value in summary.items():
        shown = "insufficient" if value is None else (
            round(value, 1) if isinstance(value, float) else value)
        print(f"{key}: {shown}")
    print("run complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
