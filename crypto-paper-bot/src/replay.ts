import { getKlines } from "./market";
import { decide } from "./strategy";
import { STOP_LOSS_THRESHOLD, TRAILING_STOP_THRESHOLD } from "./risk";
import { BotConfig } from "./types";
import {
  appendLedgerRow,
  appendLearning,
  readLedger,
  readLearnings,
  LedgerMode,
  LedgerRow,
} from "./memory";
import { checkSkip, hasAnyPriorLoss } from "./adaptiveFilter";

export interface ReplaySummary {
  symbol: string;
  interval: string;
  candleCount: number;
  trades: number;
  wins: number;
  losses: number;
  skips: number;
  totalReturnPct: number;
  maxDrawdownPct: number;
}

function setupKeyFor(config: BotConfig): string {
  return `ema${config.fastLength}-${config.slowLength}`;
}

// Shared simulation for both modes: "raw" ignores memory entirely (the
// honest baseline) and only ever writes real outcomes; "memory" reads
// data/ledger.csv + data/learnings.md first and can SKIP a BUY that
// matches a real recorded loss. No fake losses or invented candles are
// ever introduced by this function in either mode.
export async function runReplay(config: BotConfig, mode: LedgerMode): Promise<ReplaySummary> {
  const candles = await getKlines(config.symbol, config.interval, 1000);
  const setupKey = setupKeyFor(config);

  const ledger: LedgerRow[] = mode === "memory" ? readLedger() : [];
  const learningsText = mode === "memory" ? readLearnings() : "";

  if (mode === "memory" && !hasAnyPriorLoss(ledger)) {
    console.log(
      "[replay:memory] No prior recorded losses yet in data/ledger.csv — memory has nothing real to filter on yet. Run `npm run replay:raw` first to build real trade history."
    );
  }

  let equity = 1;
  let peakEquity = 1;
  let maxDrawdown = 0;
  let position: { entryPrice: number; entryTime: number; peakPrice: number } | null = null;
  let trades = 0;
  let wins = 0;
  let losses = 0;
  let skips = 0;

  for (let i = config.slowLength + 1; i < candles.length; i++) {
    const window = candles.slice(0, i + 1);
    const decision = decide(window, config);
    const price = decision.price;
    const timestamp = new Date(decision.time).toISOString();

    if (position) {
      position.peakPrice = Math.max(position.peakPrice, price);
      const stopLossPrice = position.entryPrice * (1 - STOP_LOSS_THRESHOLD);
      const trailingStopPrice = position.peakPrice * (1 - TRAILING_STOP_THRESHOLD);
      const hitStop = price <= stopLossPrice;
      const hitTrailing = price <= trailingStopPrice;
      const signalExit = decision.signal === "SELL";

      if (hitStop || hitTrailing || signalExit) {
        const returnPct = (price - position.entryPrice) / position.entryPrice;
        equity *= 1 + returnPct;
        trades += 1;
        const outcome = returnPct > 0 ? "WIN" : "LOSS";
        if (outcome === "WIN") wins += 1;
        else losses += 1;

        const exitReason = hitStop
          ? `stop-loss (-${STOP_LOSS_THRESHOLD * 100}% from entry)`
          : hitTrailing
          ? `trailing stop (-${TRAILING_STOP_THRESHOLD * 100}% from peak)`
          : "EMA signal crossover exit";

        const sellRow: LedgerRow = {
          timestamp,
          symbol: config.symbol,
          action: "SELL",
          price,
          quantity: config.quantity,
          reason: exitReason,
          mode,
          outcome,
          pnl: `${(returnPct * 100).toFixed(2)}%`,
        };
        appendLedgerRow(sellRow);
        ledger.push(sellRow);

        if (mode === "raw" && outcome === "LOSS") {
          appendLearning(
            `${config.symbol}: BUY on ${setupKey} crossover at $${position.entryPrice.toFixed(
              2
            )} (${new Date(position.entryTime).toISOString()}) lost ${(returnPct * 100).toFixed(
              2
            )}%, exited via ${exitReason} at $${price.toFixed(2)} on ${timestamp}.`
          );
        }

        position = null;
      }
    } else if (decision.signal === "BUY") {
      if (mode === "memory") {
        const check = checkSkip(config.symbol, setupKey, ledger, learningsText);
        if (check.skip) {
          skips += 1;
          const skipRow: LedgerRow = {
            timestamp,
            symbol: config.symbol,
            action: "SKIP",
            price,
            quantity: 0,
            reason: check.reason,
            mode,
            outcome: "",
            pnl: "",
          };
          appendLedgerRow(skipRow);
          ledger.push(skipRow);
          console.log(`[replay:memory] SKIP ${config.symbol} @ $${price} — ${check.reason}`);
          continue;
        }
      }

      position = { entryPrice: price, entryTime: decision.time, peakPrice: price };
      const buyRow: LedgerRow = {
        timestamp,
        symbol: config.symbol,
        action: "BUY",
        price,
        quantity: config.quantity,
        reason: decision.reason,
        mode,
        outcome: "",
        pnl: "",
      };
      appendLedgerRow(buyRow);
      ledger.push(buyRow);
    }

    peakEquity = Math.max(peakEquity, equity);
    maxDrawdown = Math.max(maxDrawdown, (peakEquity - equity) / peakEquity);
  }

  return {
    symbol: config.symbol,
    interval: config.interval,
    candleCount: candles.length,
    trades,
    wins,
    losses,
    skips,
    totalReturnPct: (equity - 1) * 100,
    maxDrawdownPct: maxDrawdown * 100,
  };
}
