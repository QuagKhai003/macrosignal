"""Weekly batch entry point.

@context  The one command the user runs every Saturday (after Friday's CFTC
          release). Phase 0 shape: validate the registry, open the db, leave
          a journal row — the honesty ledger starts on day one.
@done     0.4: loads signals.yaml (admission test enforced), inits signals.db,
          mirrors the registry into `series`, writes one 'run' journal row,
          prints "run complete".
@todo     Phase 1: call the fetchers; Phase 2: state machine + report.
@limits   Deterministic and offline in Phase 0 — no network. Every number in
          the db comes from the frozen formulas, never an LLM (Golden Rule).
@affects  src/db.py, src/registry.py, signals.db.
"""

import datetime as dt

from src import db, registry


def main(db_path=db.DB_PATH, registry_path=registry.REGISTRY_PATH,
         today: dt.date | None = None) -> int:
    today = today or dt.date.today()
    entries = registry.load_registry(registry_path, as_of=today)
    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        for e in entries:
            conn.execute(
                "INSERT OR REPLACE INTO series VALUES (?, ?, ?, ?, ?, ?)",
                (e["series_id"], e["source"], e["source_url"],
                 e["schedule"], e["window"], e["causal_sentence"]))
        conn.execute(
            "INSERT INTO journal (date, market_id, event_type, detail,"
            " price_at_event) VALUES (?, NULL, 'run', ?, NULL)",
            (today.isoformat(), f"run complete - {len(entries)} signals registered"))
        conn.commit()
    finally:
        conn.close()
    print("run complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
