import { BotConfig, PositionState, RiskDecision, StrategyDecision } from "./types";

// Thresholds fixed per trading_bot_instructions.md section 4 — no
// performance-based auto-tuning of these values is allowed. Exported so
// replay.ts's simulation uses the exact same numbers instead of a second
// hardcoded copy that could drift out of sync.
export const STOP_LOSS_THRESHOLD = 0.15;
export const TRAILING_STOP_THRESHOLD = 0.2;
const MAX_DRAWDOWN_THRESHOLD = 0.25;

export function applyRisk(
  decision: StrategyDecision,
  state: PositionState,
  currentEquity: number,
  config: BotConfig
): RiskDecision {
  if (state.equityBaseline > 0) {
    const drawdown = (state.equityBaseline - currentEquity) / state.equityBaseline;
    if (drawdown >= MAX_DRAWDOWN_THRESHOLD) {
      return {
        approved: false,
        signal: "HOLD",
        reason: `Max drawdown kill-switch tripped: equity down ${(drawdown * 100).toFixed(
          1
        )}% from baseline ${state.equityBaseline}. Halting until manual review.`,
        quantity: 0,
      };
    }
  }

  if (state.inPosition && state.entryPrice !== null && state.peakPrice !== null) {
    const stopLossPrice = state.entryPrice * (1 - STOP_LOSS_THRESHOLD);
    const trailingStopPrice = state.peakPrice * (1 - TRAILING_STOP_THRESHOLD);

    if (decision.price <= stopLossPrice) {
      return {
        approved: true,
        signal: "SELL",
        reason: `Stop-loss: price ${decision.price} <= ${stopLossPrice.toFixed(
          2
        )} (${STOP_LOSS_THRESHOLD * 100}% below entry ${state.entryPrice})`,
        quantity: config.quantity,
      };
    }

    if (decision.price <= trailingStopPrice) {
      return {
        approved: true,
        signal: "SELL",
        reason: `Trailing stop: price ${decision.price} <= ${trailingStopPrice.toFixed(
          2
        )} (${TRAILING_STOP_THRESHOLD * 100}% below peak ${state.peakPrice})`,
        quantity: config.quantity,
      };
    }
  }

  if (decision.signal === "BUY") {
    if (state.inPosition) {
      return {
        approved: false,
        signal: "HOLD",
        reason: "Already in a position; max 1 concurrent position, no pyramiding.",
        quantity: 0,
      };
    }
    return {
      approved: true,
      signal: "BUY",
      reason: decision.reason,
      quantity: config.quantity,
    };
  }

  if (decision.signal === "SELL") {
    if (!state.inPosition) {
      return {
        approved: false,
        signal: "HOLD",
        reason: "SELL signal but flat; nothing to exit.",
        quantity: 0,
      };
    }
    return {
      approved: true,
      signal: "SELL",
      reason: decision.reason,
      quantity: config.quantity,
    };
  }

  return {
    approved: false,
    signal: "HOLD",
    reason: decision.reason,
    quantity: 0,
  };
}
