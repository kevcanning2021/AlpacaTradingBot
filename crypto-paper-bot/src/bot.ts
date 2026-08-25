import { getKlines } from "./market";
import { decide } from "./strategy";
import { applyRisk } from "./risk";
import { loadState } from "./state";
import { logEvent } from "./log";
import { readLedger, readLearnings } from "./memory";
import { checkSkip } from "./adaptiveFilter";
import { BotConfig } from "./types";

// Order placement (Alpaca MCP, paper account) is still not wired up —
// trading_bot_instructions.md Definition of Done #1 blocks going live
// until the TradingView 9/21 EMA backtest has actually been run and
// reviewed. This command is analysis-only.
export async function runScan(config: BotConfig): Promise<void> {
  const candles = await getKlines(config.symbol, config.interval, config.slowLength + 50);
  const decision = decide(candles, config);
  const state = loadState();

  console.log(
    `[heartbeat] ${new Date().toISOString()} price=${decision.price} ema${config.fastLength}=${decision.fastMA.toFixed(
      2
    )} ema${config.slowLength}=${decision.slowMA.toFixed(2)} signal=${decision.signal}`
  );

  if (decision.signal === "BUY") {
    const setupKey = `ema${config.fastLength}-${config.slowLength}`;
    const check = checkSkip(config.symbol, setupKey, readLedger(), readLearnings());
    if (check.skip) {
      console.log(`[decision] SKIP ${config.symbol} — ${check.reason}`);
      logEvent(`Scan decision: SKIP ${config.symbol} - ${check.reason}`);
    } else {
      const risk = applyRisk(decision, state, state.equityBaseline || decision.price, config);
      const line = `${risk.approved ? "APPROVED" : "BLOCKED"} ${risk.signal} - ${risk.reason}`;
      console.log(`[decision] ${line}`);
      logEvent(`Scan decision: ${line}`);
    }
  } else if (decision.signal === "SELL") {
    const risk = applyRisk(decision, state, state.equityBaseline || decision.price, config);
    const line = `${risk.approved ? "APPROVED" : "BLOCKED"} ${risk.signal} - ${risk.reason}`;
    console.log(`[decision] ${line}`);
    logEvent(`Scan decision: ${line}`);
  }

  console.log(
    "[safety] Order placement is disabled: no verified TradingView backtest yet (trading_bot_instructions.md, Definition of Done #1). This scan is analysis-only, nothing was sent to Alpaca."
  );
}
