import { LedgerRow } from "./memory";

export interface SkipCheck {
  skip: boolean;
  reason: string;
}

// Only ever looks at rows/text that already exist — never seeds or assumes
// a loss. "Similar setup" is scoped to the same symbol + same strategy
// signature (setupKey), since the current strategy has exactly one setup
// per symbol; a future multi-strategy bot would widen setupKey, not this
// matching logic.
export function checkSkip(
  symbol: string,
  setupKey: string,
  ledger: LedgerRow[],
  learningsText: string
): SkipCheck {
  const priorLoss = ledger.find(
    (row) => row.symbol === symbol && row.action === "SELL" && row.outcome === "LOSS"
  );

  if (priorLoss) {
    return {
      skip: true,
      reason:
        `Prior real loss on ${symbol} (setup ${setupKey}): sold at $${priorLoss.price} on ` +
        `${priorLoss.timestamp} for ${priorLoss.pnl} (${priorLoss.reason}). Skipping repeat of this setup.`,
    };
  }

  if (learningsText.toLowerCase().includes(symbol.toLowerCase())) {
    return {
      skip: true,
      reason: `data/learnings.md contains a note mentioning ${symbol} — skipping until reviewed manually.`,
    };
  }

  return { skip: false, reason: "" };
}

export function hasAnyPriorLoss(ledger: LedgerRow[]): boolean {
  return ledger.some((row) => row.outcome === "LOSS");
}
