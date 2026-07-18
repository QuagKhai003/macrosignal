// The no-jargon dictionary (UI guideline §3) — the ONE source of truth for
// every user-facing string. Internal terms never reach the screen.
export const STATE = {
  NEUTRAL: { phrase: "Nothing to see", mark: "○", cls: "neutral" },
  EARLY: { phrase: "Worth watching — too soon to buy", mark: "●", cls: "watch" },
  CONFIRMED: { phrase: "Green light — entry allowed", mark: "▲", cls: "go" },
  CROWDED: { phrase: "Too popular — do not enter", mark: "◆", cls: "crowd" },
  BROKEN: { phrase: "Story broke — exit or ignore", mark: "✕", cls: "broke" },
};

export const WEATHER = {
  GREEN: { phrase: "Calm — normal sizes", mark: "▲", cls: "calm" },
  YELLOW: { phrase: "Caution — half sizes", mark: "◆", cls: "caution" },
  RED: { phrase: "Storm risk — no new buying", mark: "✕", cls: "storm" },
};

export const MARKET_NAME = {
  gold: "Gold", wti: "Oil", ust10y: "US government bonds", eur: "Euro",
  corn: "Corn", silver: "Silver", copper: "Copper", natgas: "Natural gas",
  semis: "Semiconductor stocks",
};

// The four question cards (§7): title + plain answers per stored score.
export function questionCards(scores) {
  const engine =
    scores.engine === true ? "The reason to own this is getting stronger."
    : scores.engine === false ? "The reason to own this is getting weaker."
    : "No reliable reason-signal exists for this market.";
  const party =
    scores.party_pct == null ? "Not enough positioning data yet."
    : scores.party_pct >= 80 ? "The trade is nearly full."
    : scores.party_pct <= 20 ? "The trade is nearly empty."
    : "The trade is partly occupied.";
  const trend =
    scores.momentum === 1 ? "Price is moving."
    : scores.momentum === 0 ? "Price is not moving yet."
    : "Not enough price history yet.";
  const news =
    scores.news === "loud_greedy" ? "Everyone is talking about it — late."
    : scores.news === "loud_scared" ? "Loud and fearful headlines."
    : scores.news === "quiet" ? "Nobody is talking about it."
    : "Not enough news history yet.";
  return [
    { title: "The reason", answer: engine,
      value: scores.engine == null ? "—" : scores.engine ? "yes" : "no" },
    { title: "The crowd", answer: party,
      value: scores.party_pct == null ? "—"
        : `${Math.round(scores.party_pct)} / 100` },
    { title: "The trend", answer: trend,
      value: scores.momentum == null ? "—" : scores.momentum ? "up" : "flat" },
    { title: "The talk", answer: news, value: scores.news ?? "—" },
  ];
}
