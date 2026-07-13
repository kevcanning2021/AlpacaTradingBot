import { BotConfig, Candle, StrategyDecision } from "./types";

function ema(values: number[], period: number): number[] {
  const k = 2 / (period + 1);
  const result: number[] = [values[0]];
  for (let i = 1; i < values.length; i++) {
    result.push(values[i] * k + result[i - 1] * (1 - k));
  }
  return result;
}

export function decide(candles: Candle[], config: BotConfig): StrategyDecision {
  if (candles.length < config.slowLength + 2) {
    throw new Error(
      `Need at least ${config.slowLength + 2} candles to evaluate a crossover, got ${candles.length}.`
    );
  }

  const closes = candles.map((c) => c.close);
  const fast = ema(closes, config.fastLength);
  const slow = ema(closes, config.slowLength);

  const last = closes.length - 1;
  const prevFast = fast[last - 1];
  const prevSlow = slow[last - 1];
  const curFast = fast[last];
  const curSlow = slow[last];

  const crossedUp = prevFast <= prevSlow && curFast > curSlow;
  const crossedDown = prevFast >= prevSlow && curFast < curSlow;

  let signal: StrategyDecision["signal"] = "HOLD";
  let reason = `EMA${config.fastLength}=${curFast.toFixed(2)} vs EMA${config.slowLength}=${curSlow.toFixed(
    2
  )}, no crossover`;

  if (crossedUp) {
    signal = "BUY";
    reason = `EMA${config.fastLength} crossed above EMA${config.slowLength}`;
  } else if (crossedDown) {
    signal = "SELL";
    reason = `EMA${config.fastLength} crossed below EMA${config.slowLength}`;
  }

  return {
    signal,
    reason,
    fastMA: curFast,
    slowMA: curSlow,
    price: closes[last],
    time: candles[last].closeTime,
  };
}
