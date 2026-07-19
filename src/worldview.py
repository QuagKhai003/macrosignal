"""The world right now — one deterministic picture from every side.

@context  The user's vision, verbatim intent: numbers + news + actors read
          TOGETHER — "missing a side will not accurately picture the whole
          world." Every ingredient already exists in the weekly readouts;
          this module assembles them into fixed plain-language sentences.
          No LLM writes here (Golden Rule; the calm-referee voice): each
          sentence is a frozen template triggered by a numeric condition,
          auditable back to its number.
@done     lines(): the money tide (net liquidity level + direction), the
          price of money (dollar percentile), market temperature (valuation
          + credit + manager cash), the crowd (fullest/emptiest trades, loud
          news), the actors (whale ledger direction, insider clusters), and
          the disagreement callouts (records-with-defensive-whales;
          scared-news-empty-trade). Sections appear only when their inputs
          exist — a missing side says so instead of pretending.
@todo     Dashboard panel — next batch.
@limits   PURE over the readout dicts weekly_run already computes; no db,
          no network. Thresholds are the registry/spec ones already in use
          (80/20 party, 90th valuation, 4% cash), never invented here.
@affects  src/report.py ("The world right now" section); weekly_run.
"""

from src.report import MARKET_NAME


def lines(summary: dict, weather: str, results: dict,
          whale_ledger: list | None = None,
          insider_flags: dict | None = None,
          foreign: dict | None = None,
          insider_detail: dict | None = None,
          edgar_events: dict | None = None) -> list[str]:
    out = []

    # niche actor A2: foreign-government demand for US assets (two windows)
    foreign = foreign or {}
    custody = foreign.get("custody_change_4w")
    if custody is not None:
        direction = "adding to" if custody > 0 else "draining"
        out.append(f"Foreign central banks are {direction} their US holdings"
                   f" ({custody:+.1f}% over 4 weeks, weekly custody data).")
    indirect = foreign.get("indirect_share_pct")
    indirect_avg = foreign.get("indirect_share_avg_pct")
    if indirect is not None and indirect_avg is not None:
        vs = "above" if indirect >= indirect_avg else "below"
        out.append(f"Foreign demand at the latest note auction was"
                   f" {indirect:.0f}% ({vs} its recent {indirect_avg:.0f}%"
                   f" average).")

    liq = summary.get("net_liquidity_pct")
    if liq is not None:
        tide = "draining" if summary.get("net_liquidity_falling") else "rising"
        level = ("high" if liq >= 70 else "low" if liq <= 30 else "middling")
        out.append(f"The money tide is {tide} from a {level} level"
                   f" ({liq:.0f} of 100 vs the last decade).")

    usd = summary.get("us_dollar_pct")
    if usd is not None:
        strength = ("strong" if usd >= 70 else "weak" if usd <= 30
                    else "middling")
        out.append(f"The dollar is {strength} ({usd:.0f} of 100) —"
                   f" {'a squeeze on' if usd >= 70 else 'room for'}"
                   f" commodities and foreign markets.")

    val = summary.get("market_valuation_pct")
    cash = summary.get("weather_manager_cash")
    spread = summary.get("credit_spread_pct")
    if val is not None:
        heat = ("priced near records" if val >= 90
                else "cheap vs its history" if val <= 30 else "mid-priced")
        out.append(f"The US stock market is {heat} ({val:.0f} of 100 vs 20"
                   f" years).")
    if cash is not None and cash < 4.0:
        out.append(f"Professional managers hold only {cash:.1f}% spare cash"
                   f" — almost nobody is left to buy the next dip.")
    if spread is not None and spread <= 20:
        out.append("Credit markets are calm — lenders smell no trouble yet.")
    elif spread is not None and spread >= 80:
        out.append("Credit markets are stressed — lenders are demanding"
                   " danger pay.")

    full = [m for m, r in results.items()
            if r.get("party_pct") is not None and r["party_pct"] >= 80]
    empty = [m for m, r in results.items()
             if r.get("party_pct") is not None and r["party_pct"] <= 20]
    if full:
        out.append("Crowded trades: "
                   + ", ".join(MARKET_NAME[m] for m in full) + ".")
    if empty:
        out.append("Abandoned trades: "
                   + ", ".join(MARKET_NAME[m] for m in empty) + ".")
    loud = [m for m, r in results.items()
            if r.get("news") == "loud_greedy"]
    if loud:
        out.append("Everyone is already talking about: "
                   + ", ".join(MARKET_NAME[m] for m in loud) + ".")

    if whale_ledger:
        known = [w for w in whale_ledger if w["prior"] is not None]
        if known:
            grew = sum(1 for w in known if w["total"] > w["prior"])
            stance = ("adding" if grew > len(known) / 2 else "cutting"
                      if grew < len(known) / 2 else "split")
            out.append(f"The tracked whales are {stance}: {grew} of"
                       f" {len(known)} grew stock exposure last quarter.")
    clustered = sorted(t for t, on in (insider_flags or {}).items() if on)
    if clustered:
        out.append("Insiders are cluster-buying at: "
                   + ", ".join(clustered) + ".")
    # Tier-2: elevate the high-quality clusters (opportunistic and/or CFO)
    for ticker in clustered:
        d = (insider_detail or {}).get(ticker, {})
        marks = []
        if d.get("opportunistic"):
            marks.append("off their usual pattern")
        if d.get("cfo"):
            marks.append("including the CFO")
        if marks:
            out.append(f"  — {ticker}'s buying is high-quality ("
                       + ", ".join(marks) + ").")

    # Tier-3 (A4): activist stakes + insider sale-intent on the equity universe
    for label, tickers in sorted((edgar_events or {}).items()):
        out.append(f"Recent {label} filing(s): " + ", ".join(tickers) + ".")

    # niche actor A3: whale concentration — few hands dominating a market
    concentrated = [MARKET_NAME[m] for m, r in results.items()
                    if r.get("conc_pct") is not None and r["conc_pct"] >= 45]
    if concentrated:
        out.append("Held by few hands (top-4 traders > 45% of the market): "
                   + ", ".join(concentrated) + ".")

    # the disagreements — the contrast is the signal
    if (val is not None and val >= 90 and whale_ledger
            and any(w["prior"] is not None and w["total"] < w["prior"]
                    for w in whale_ledger)):
        out.append("Disagreement worth noting: prices near records while"
                   " whales cut exposure — someone is wrong.")
    scared = [m for m, r in results.items() if r.get("scared_and_abandoned")]
    if scared:
        out.append("Early-type contrast: scary headlines over empty trades"
                   " at " + ", ".join(MARKET_NAME[m] for m in scared) + ".")

    if weather == "RED":
        out.append("Overall: storm conditions — the machine buys nothing"
                   " new until the gauges ease.")
    elif weather == "YELLOW":
        out.append("Overall: caution — anything bought is half-size.")
    else:
        out.append("Overall: calm — normal operation.")
    return out
