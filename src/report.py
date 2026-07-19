"""Weekly report v0 — plain text, changes first, silence when quiet.

@context  The product's voice (UI guideline §1/§3): one sentence of silence
          by default; plain-word dictionary only — internal state names never
          reach the user. Changes lead; levels hide behind --full.
@done     build(): weather line (YELLOW stub phrase), change lines with
          "was:" context, the big "Nothing changed this week. Do nothing.",
          unchanged-count line, --full appendix (per-market card lines +
          the raw readouts with plain labels).
@todo     Phase 3: real weather phrase; Phase 4: news phrases; later the §7
          "what would change this verdict" bullets (Screen 2 material).
@limits   Pure formatting — no queries, no numbers computed here (Golden
          Rule: numbers arrive already computed).
@affects  weekly_run.py; consumes states.run_week results + spine.summarize.
"""

PHRASE = {
    "NEUTRAL": "Nothing to see",
    "EARLY": "Worth watching — too soon to buy",
    "CONFIRMED": "Green light — entry allowed",
    "CROWDED": "Too popular — do not enter",
    "BROKEN": "Story broke — exit or ignore",
}
WEATHER_PHRASE = {
    "GREEN": "Calm — normal sizes",
    "YELLOW": "Caution — half sizes",
    "RED": "Storm risk — no new buying",
}
MARKET_NAME = {"gold": "Gold", "wti": "Oil (WTI)", "ust10y": "US 10-yr note",
               "eur": "Euro", "corn": "Corn", "silver": "Silver",
               "copper": "Copper", "natgas": "Natural gas",
               "semis": "Semiconductors (theme)"}


def build(results: dict, prev_states: dict, week: str, weather: str = "YELLOW",
          summary: dict | None = None, full: bool = False,
          whale: dict | None = None, divergence: bool = False,
          alarm_banner: str | None = None,
          insider_flags: dict | None = None,
          whale_ledger: list | None = None,
          world_lines: list | None = None,
          forward_stats: dict | None = None,
          simulations: dict | None = None) -> str:
    lines = [f"Week {week}", f"Weather: {WEATHER_PHRASE[weather]}"]
    if divergence:
        lines.append("Divergence: the pros have no spare cash while the"
                     " biggest whale is hoarding it — the contrast is the"
                     " signal.")
    if alarm_banner:
        lines.append(f"Self-check: {alarm_banner}")
    lines.append("")
    changes = {m: r for m, r in results.items()
               if r["state"] != prev_states.get(m, "NEUTRAL")}
    if not changes:
        lines.append("Nothing changed this week. Do nothing.")
    else:
        for market, r in changes.items():
            was = PHRASE[prev_states.get(market, "NEUTRAL")]
            lines.append(f"{MARKET_NAME[market]}: {PHRASE[r['state']]}"
                         f" (was: {was})")
        unchanged = len(results) - len(changes)
        if unchanged:
            lines.append(f"Everything else is unchanged"
                         f" ({unchanged} markets).")
    for market, r in results.items():
        if r.get("scared_and_abandoned"):
            lines.append(f"{MARKET_NAME[market]}: Scary headlines, empty"
                         f" trade — early type.")
    flagged = sorted(t for t, on in (insider_flags or {}).items() if on)
    if flagged:
        lines.append(f"Insider cluster (F13): 3+ executives bought their own"
                     f" stock within 90 days at: {', '.join(flagged)}.")
    if world_lines:
        lines += ["", "— The world right now —"] + list(world_lines)
    if forward_stats:
        from src import forward as _fwd
        lines += ["", "— What alike-weeks did next (history, not prophecy) —"]
        for market, r in results.items():
            stats = forward_stats.get(market, {}).get(r["state"])
            lines.append(f"{MARKET_NAME[market]}: {_fwd.sentence(stats)}")
    if simulations:
        from src import simulate as _sim
        lines += ["", "— The simulation (replays of history's variety) —"]
        for market, r in results.items():
            result = simulations.get(market, {}).get(r["state"])
            lines.append(f"{MARKET_NAME[market]}: {_sim.sentence(result)}")
    if full and whale:
        frac = "?" if whale["fraction"] is None else f"{whale['fraction']:.0%}"
        was = ("" if whale["prior_fraction"] is None
               else f" (was {whale['prior_fraction']:.0%} the quarter before)")
        lines += ["", "— The whale (Berkshire, filings only) —",
                  f"Cash pile: ${whale['cash'] / 1e9:.1f}B as of"
                  f" {whale['period']} — {frac} of its investable money is"
                  f" waiting{was}.",
                  "Signal: defensive whale. Alternative reads: structural /"
                  " rotation.",
                  f"Decider: next quarterly filing, due ~{whale['decider']}."]
    if full and whale_ledger:
        lines += ["", "— The whale ledger (13F filings, ~45 days delayed) —"]
        grew = sum(1 for w in whale_ledger
                   if w["prior"] is not None and w["total"] > w["prior"])
        known = sum(1 for w in whale_ledger if w["prior"] is not None)
        lines.append(f"{grew} of {known} tracked whales grew their US stock"
                     f" exposure last quarter.")
        for w in whale_ledger:
            move = ("?" if w["prior"] is None else
                    f"{(w['total'] / w['prior'] - 1) * 100:+.0f}%")
            lines.append(f"  {w['name']}: ${w['total'] / 1e9:.1f}B as of"
                         f" {w['period']} ({move} vs prior quarter)")
    if full:
        lines += ["", "— The numbers —"]
        for market, r in results.items():
            party = ("?" if r["party_pct"] is None
                     else f"{r['party_pct']:.0f} of 100")
            momentum = {1: "moving", 0: "not moving", None: "?"}[r["momentum"]]
            lines.append(
                f"{MARKET_NAME[market]}: {PHRASE[r['state']]} | how full:"
                f" {party} | price: {momentum} | size: {r['size_fraction']:.4f}")
        for key, value in (summary or {}).items():
            shown = "insufficient" if value is None else (
                round(value, 1) if isinstance(value, float) else value)
            lines.append(f"{key}: {shown}")
    return "\n".join(lines)
