// Verdict sentence (§3: state phrase + the single strongest reason, one
// reason only) and "what would change this verdict" (§7: the plan, 1-2
// bullets, plain words). Deterministic mappings over stored scores — the
// dashboard judges nothing itself.
import { STATE } from "./dictionary";

export function verdictSentence(state, scores) {
  const phrase = STATE[state].phrase;
  const reason = strongestReason(state, scores);
  return reason ? `${phrase} — ${reason}` : phrase;
}

function strongestReason(state, s) {
  switch (state) {
    case "CROWDED":
      return s.party_pct != null
        ? `${Math.round(s.party_pct / 10)} of 10 investors already own it`
        : "everyone is already talking about it";
    case "CONFIRMED":
      return "the reason holds and price is moving";
    case "EARLY":
      return "a reason exists but price has not confirmed";
    case "BROKEN":
      return "its reason stopped working";
    default:
      if (s.engine === true) return "price has not started moving";
      if (s.engine === false) return "no reason to own it right now";
      return null; // bare phrase — nothing to explain
  }
}

export function whatWouldChange(state, s) {
  switch (state) {
    case "NEUTRAL":
      return s.engine === true
        ? ["If price starts moving up and holds for two weeks, this becomes worth watching."]
        : ["If a reason to own it appears (its driver turns favorable), this becomes worth watching."];
    case "EARLY":
      return ["If the price trend confirms, this becomes a green light.",
              "If the reason fades first, this goes back to nothing."];
    case "CONFIRMED":
      return ["If the trade fills up while headlines turn greedy, this becomes too popular.",
              "If its reason dies and stays dead for half a year, the story breaks."];
    case "CROWDED":
      return ["If the crowd thins out, this can reset and be considered again."];
    case "BROKEN":
      return ["If a fresh reason appears and holds, this restarts as worth watching."];
    default:
      return [];
  }
}
