"""Weekly batch entry point.

@context  The one command the user runs every Saturday (after Friday's CFTC
          release). Phase 0 stub proving the repo can run end to end.
@done     Prints "run complete", exits 0 (batch 0.1).
@todo     0.4: load signals.yaml, open signals.db, write one journal row per run.
@limits   Deterministic and offline in Phase 0 — no network.
@affects  Will orchestrate src/ (fetchers, formulas, state engine, report).
"""


def main() -> int:
    print("run complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
