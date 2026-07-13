import { runScan } from "./bot";
import { runReplay, ReplaySummary } from "./replay";
import { resetMemory } from "./memory";
import { getConfig } from "./config";

function printReplaySummary(label: string, summary: ReplaySummary): void {
  console.log(`[${label}] ${summary.symbol} ${summary.interval}, ${summary.candleCount} candles`);
  console.log(
    `[${label}] trades=${summary.trades} wins=${summary.wins} losses=${summary.losses} ` +
      `skips=${summary.skips} totalReturn=${summary.totalReturnPct.toFixed(
        2
      )}% maxDrawdown=${summary.maxDrawdownPct.toFixed(2)}%`
  );
}

async function main(): Promise<void> {
  const command = process.argv[2];
  const config = getConfig();

  if (command === "scan") {
    await runScan(config);
  } else if (command === "replay:raw") {
    const summary = await runReplay(config, "raw");
    printReplaySummary("replay:raw", summary);
    console.log(
      "[replay:raw] Honest baseline — ignores data/ledger.csv and data/learnings.md entirely. Not a substitute for the required TradingView in-sample/out-of-sample backtest (trading_bot_instructions.md Definition of Done #1)."
    );
  } else if (command === "replay:memory") {
    const summary = await runReplay(config, "memory");
    printReplaySummary("replay:memory", summary);
    console.log(
      "[replay:memory] Memory-aware pass — only skips a BUY when data/ledger.csv shows a real prior loss on this symbol/setup, or data/learnings.md mentions it. See action=SKIP rows in data/ledger.csv for details."
    );
  } else if (command === "memory:reset") {
    resetMemory();
    console.log("[memory:reset] data/ledger.csv and data/learnings.md reset to empty.");
  } else {
    console.error(`Unknown command "${command}". Use "scan", "replay:raw", "replay:memory", or "memory:reset".`);
    process.exit(1);
  }
}

main().catch((err) => {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
});
