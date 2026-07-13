// Backtest verification ONLY — this is not the bot. No broker, no orders,
// no fake data. Pulls real BTC/USDT and ETH/USDT candles from Binance's
// public klines endpoint and simulates the exact rules in
// trading_bot_instructions.md: RSI(14) 30/70, 25%-of-equity sizing, 15%
// stop-loss, 25% total-portfolio drawdown kill-switch, max 2 concurrent
// positions (one per symbol). Run with: node backtest.js [interval] [candleCount]
// e.g. node backtest.js 1d 500

const BASE = "https://api.binance.com/api/v3/klines";
const SYMBOLS = { BTC: "BTCUSDT", ETH: "ETHUSDT" };
const INTERVAL = process.argv[2] || "1h";
const CANDLES_WANTED = process.argv[3] ? parseInt(process.argv[3], 10) : 4000;
const IN_SAMPLE_FRACTION = 0.7;

async function fetchKlinesBatch(symbol, endTime) {
  const url = `${BASE}?symbol=${symbol}&interval=${INTERVAL}&limit=1000${
    endTime ? `&endTime=${endTime}` : ""
  }`;
  const res = await fetch(url);
  if (!res.ok) {
    const body = await res.text().catch(() => "<no body>");
    throw new Error(`Binance klines failed (${res.status} ${res.statusText}) for ${symbol}: ${body}`);
  }
  const raw = await res.json();
  if (!Array.isArray(raw) || raw.length === 0) {
    throw new Error(`Binance returned no candle data for ${symbol} ending ${endTime ?? "now"}.`);
  }
  return raw.map((r) => ({
    openTime: r[0],
    close: parseFloat(r[4]),
  }));
}

async function fetchHistory(symbol, totalWanted) {
  let all = [];
  let endTime;
  while (all.length < totalWanted) {
    const batch = await fetchKlinesBatch(symbol, endTime);
    all = batch.concat(all);
    endTime = batch[0].openTime - 1;
    if (batch.length < 1000) break; // hit start of available history
  }
  return all.slice(-totalWanted);
}

function computeRSI(closes, period = 14) {
  const rsi = new Array(closes.length).fill(null);
  let gainSum = 0;
  let lossSum = 0;
  for (let i = 1; i <= period; i++) {
    const change = closes[i] - closes[i - 1];
    if (change >= 0) gainSum += change;
    else lossSum -= change;
  }
  let avgGain = gainSum / period;
  let avgLoss = lossSum / period;
  rsi[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);

  for (let i = period + 1; i < closes.length; i++) {
    const change = closes[i] - closes[i - 1];
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? -change : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    rsi[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return rsi;
}

function runSimulation(rows, startIdx, endIdx, oversold, overbought) {
  let equity = 1;
  let peakEquity = 1;
  let maxDrawdown = 0;
  let drawdownHalted = false;
  const positions = { BTC: null, ETH: null };
  const trades = { BTC: { wins: 0, losses: 0 }, ETH: { wins: 0, losses: 0 } };

  for (let i = startIdx; i < endIdx; i++) {
    const row = rows[i];
    for (const sym of ["BTC", "ETH"]) {
      const data = row[sym];
      if (data.rsi === null) continue;
      const pos = positions[sym];

      if (pos) {
        const stopPrice = pos.entry * 0.85;
        const hitStop = data.price <= stopPrice;
        const hitSignal = data.rsi > overbought;
        if (hitStop || hitSignal) {
          const ret = (data.price - pos.entry) / pos.entry;
          equity += pos.sizeFraction * ret;
          if (ret > 0) trades[sym].wins += 1;
          else trades[sym].losses += 1;
          positions[sym] = null;
        }
      } else if (!drawdownHalted && data.rsi < oversold) {
        positions[sym] = { entry: data.price, sizeFraction: equity * 0.25 };
      }
    }

    peakEquity = Math.max(peakEquity, equity);
    const dd = (peakEquity - equity) / peakEquity;
    maxDrawdown = Math.max(maxDrawdown, dd);
    if (dd >= 0.25) drawdownHalted = true;
  }

  const totalTrades =
    trades.BTC.wins + trades.BTC.losses + trades.ETH.wins + trades.ETH.losses;

  return {
    totalTrades,
    btc: trades.BTC,
    eth: trades.ETH,
    totalReturnPct: (equity - 1) * 100,
    maxDrawdownPct: maxDrawdown * 100,
  };
}

function printResult(label, r) {
  console.log(`[${label}] trades=${r.totalTrades} (BTC ${r.btc.wins}W/${r.btc.losses}L, ETH ${r.eth.wins}W/${r.eth.losses}L) totalReturn=${r.totalReturnPct.toFixed(2)}% maxDrawdown=${r.maxDrawdownPct.toFixed(2)}%`);
}

async function main() {
  console.log(`Fetching real ${INTERVAL} candles from Binance (public endpoint, no API key)...`);
  const [btcCandles, ethCandles] = await Promise.all([
    fetchHistory(SYMBOLS.BTC, CANDLES_WANTED),
    fetchHistory(SYMBOLS.ETH, CANDLES_WANTED),
  ]);

  const btcByTime = new Map(btcCandles.map((c) => [c.openTime, c.close]));
  const ethByTime = new Map(ethCandles.map((c) => [c.openTime, c.close]));
  const commonTimes = btcCandles
    .map((c) => c.openTime)
    .filter((t) => ethByTime.has(t))
    .sort((a, b) => a - b);

  if (commonTimes.length < 200) {
    throw new Error(
      `Only ${commonTimes.length} aligned BTC/ETH candles found — too few to backtest meaningfully.`
    );
  }

  const btcCloses = commonTimes.map((t) => btcByTime.get(t));
  const ethCloses = commonTimes.map((t) => ethByTime.get(t));
  const btcRSI = computeRSI(btcCloses);
  const ethRSI = computeRSI(ethCloses);

  const rows = commonTimes.map((t, i) => ({
    time: t,
    BTC: { price: btcCloses[i], rsi: btcRSI[i] },
    ETH: { price: ethCloses[i], rsi: ethRSI[i] },
  }));

  const startDate = new Date(rows[0].time).toISOString();
  const endDate = new Date(rows[rows.length - 1].time).toISOString();
  console.log(`Aligned ${rows.length} ${INTERVAL} candles for BTC/ETH: ${startDate} -> ${endDate}\n`);

  const splitIdx = Math.floor(rows.length * IN_SAMPLE_FRACTION);
  const inSampleLabel = `in-sample (${new Date(rows[0].time).toISOString().slice(0, 10)} -> ${new Date(rows[splitIdx].time).toISOString().slice(0, 10)})`;
  const outOfSampleLabel = `out-of-sample (${new Date(rows[splitIdx].time).toISOString().slice(0, 10)} -> ${new Date(rows[rows.length - 1].time).toISOString().slice(0, 10)})`;

  const THRESHOLD_PAIRS = [
    [30, 70],
    [25, 75],
    [20, 80],
    [35, 65],
  ];

  for (const [oversold, overbought] of THRESHOLD_PAIRS) {
    console.log(`=== RSI(14) ${oversold}/${overbought} ===`);
    printResult("full-window", runSimulation(rows, 0, rows.length, oversold, overbought));
    printResult(inSampleLabel, runSimulation(rows, 0, splitIdx, oversold, overbought));
    printResult(outOfSampleLabel, runSimulation(rows, splitIdx, rows.length, oversold, overbought));
    console.log("");
  }

  console.log(
    "This is a backtest verification only — real Binance data, no fabrication, no broker connection, no orders placed. Review the in-sample vs out-of-sample gap for signs of overfitting before treating any of these threshold pairs as validated."
  );
}

main().catch((err) => {
  console.error("BLOCKED:", err instanceof Error ? err.message : String(err));
  process.exit(1);
});
