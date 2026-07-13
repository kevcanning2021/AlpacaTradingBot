# crypto-paper-bot

A paper-trading-only BTC/USDT bot: a 9/21 EMA crossover strategy, a
memory system that skips a signal if it real-recorded a loss on the same
setup before, and a backtest replay engine. **There is no broker/MCP
adapter and no order-placing code path in this repo at all** — every
command below only reads public market data and/or simulates. See
[trading_bot_instructions.md](../trading_bot_instructions.md) at the repo
root for the full safety/strategy contract this bot follows.

## What it does

- Fetches real BTCUSDT candles from Binance's public klines endpoint (no
  API key needed).
- Computes a 9/21 EMA crossover signal (configurable).
- `scan`: prints one live heartbeat + decision for the current candle.
  Analysis only — never places an order.
- `replay:raw`: backtests the strategy over the last 1000 real candles,
  ignoring memory entirely. This is the honest baseline.
- `replay:memory`: same backtest, but before each BUY it checks
  `data/ledger.csv` and `data/learnings.md` for a real prior loss on this
  symbol/setup and SKIPs if one exists.
- `memory:reset`: wipes the two memory files back to empty.

## Install

```
npm install
cp .env.example .env   # optional — defaults work with no .env at all
```

## Running it

```
npm run scan            # one live heartbeat + decision, analysis only
npm run replay:raw       # backtest baseline, ignores memory
npm run replay:memory     # backtest with memory-based skipping
npm run memory:reset      # wipe data/ledger.csv and data/learnings.md
```

Typical first pass: run `replay:raw` once to build real trade history,
then `replay:memory` to see it actually skip a repeat of a real losing
setup. Run `memory:reset` any time you want to start the learning
history over from nothing.

## Optional WhatsApp alerts

`scan` can send a WhatsApp message via CallMeBot — the same service and
env vars (`WHATSAPP_ENABLED`, `WHATSAPP_PHONE`, `WHATSAPP_APIKEY`) as the
Python bots' `WhatsAppNotifier`, so an existing CallMeBot registration
works here with no new signup. Off by default; set the three vars in
`.env` to turn it on. It only fires on a SKIP or an approved BUY/SELL
signal — never on a plain HOLD — and never places an order either way.
A failed send is logged and swallowed, it never crashes the scan.

## How paper/local execution works

Nothing in this repo places an order anywhere, paper or live. `scan` and
both `replay:*` commands compute signals and simulate outcomes locally
in-process. `state.json` (scan's position memory) and `lessons.log`
(scan's decision log) are local files written to the project root and
are gitignored — they're runtime state, not source.

## Optional paper MCP/API mode (not built yet)

`trading_bot_instructions.md` calls for eventually routing real paper
orders through Alpaca's MCP server. That adapter **does not exist in this
codebase** — adding it is a deliberate, separate step, not a config
change, and per the instructions file it's gated behind actually running
and reviewing the TradingView 9/21 backtest first. When that adapter is
built, it should:
- only ever target a paper account,
- read credentials from `.env` (never hardcoded, never logged, never
  committed — `.env` is gitignored),
- be verified with a manual smoke-test order+close before any automated
  path uses it.

Until then, treat any `broker:*` script or MCP credential as something
that doesn't exist here — there's nothing to configure.

## Where memory lives

- `data/ledger.csv` — every real BUY/SELL/SKIP the bot has made, columns:
  `timestamp,symbol,action,price,quantity,reason,mode,outcome,pnl`.
  `mode` is `raw` or `memory`, so you can see which run produced which
  row. Only `SELL` rows have `outcome`/`pnl` filled in.
- `data/learnings.md` — plain-English notes, one per real loss found by
  `replay:raw`. Never seeded, never fabricated — if no real loss happened,
  no note gets written.
- Both are gitignored (local, generated, tied to whenever you happened to
  run a backtest) and get created fresh on first run or after
  `memory:reset`.

## Changing symbol/interval/strategy/sizing

Everything tunable lives in `.env` (see `.env.example` for the full list
and defaults), read by `src/config.ts`:

| Variable      | Meaning                          | Default  |
|---------------|-----------------------------------|----------|
| `SYMBOL`      | Binance symbol                    | `BTCUSDT`|
| `INTERVAL`    | Candle size (`1m`,`5m`,`1h`, ...)  | `5m`     |
| `FAST_LENGTH` | Fast EMA period                   | `9`      |
| `SLOW_LENGTH` | Slow EMA period                   | `21`     |
| `QUANTITY`    | Simulated size per trade           | `1`      |
| `MAX_POSITION`| Max concurrent positions           | `1`      |

To try a different strategy entirely (not just different EMA lengths),
edit `src/strategy.ts`'s `decide()` — it only needs to keep returning a
`StrategyDecision` shape; `risk.ts`, `replay.ts`, and `bot.ts` don't care
how the signal was computed.

## Safety rules and limitations

- No live trading code path exists — see above. This is enforced by
  omission, not a flag: there is nothing to accidentally flip on.
- Memory quality rules: `data/ledger.csv`/`data/learnings.md` are only
  ever written from real backtest/scan outcomes. Nothing seeds a fake
  loss, invents a candle, or forces a failure.
- **Known limitation**: the memory skip-check currently keys only on
  symbol + EMA setup (there's only one setup per symbol right now), so
  once a real loss is recorded, `replay:memory` will skip *every*
  subsequent BUY signal for that symbol — it doesn't yet expire old
  losses or distinguish finer-grained setups. Confirmed live: a fresh
  `replay:memory` run after one real loss showed 0 trades / all skips.
  Widening this needs a deliberate design decision, not a silent change.
- Fixed risk thresholds (15% stop-loss, 20% trailing stop) live in
  `src/risk.ts` and are shared with `replay.ts` — no auto-tuning.
- `.env` is gitignored; `.env.example` has no real secrets and is safe to
  commit.

## Next things to try (all paper/local, nothing to break)

1. Run `npm run memory:reset`, then `replay:raw` on a different `INTERVAL`
   (e.g. `1h` in `.env`) to see if the same EMA9/21 setup still loses as
   often on a less noisy timeframe.
2. Edit `FAST_LENGTH`/`SLOW_LENGTH` in `.env` (e.g. try 12/26) and compare
   `replay:raw`'s trade count and total return against the 9/21 baseline.
3. Run `npm run scan` a few times across different points in the market
   and watch it correctly SKIP once `data/learnings.md` has a real loss on
   file — confirms the memory path also protects the "live" analysis-only
   path, not just backtests.
