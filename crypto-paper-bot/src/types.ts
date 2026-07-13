export type Signal = "BUY" | "SELL" | "HOLD";

export interface Candle {
  openTime: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  closeTime: number;
}

export interface BotConfig {
  symbol: string;
  interval: string;
  fastLength: number;
  slowLength: number;
  quantity: number;
  maxPosition: number;
}

export interface StrategyDecision {
  signal: Signal;
  reason: string;
  fastMA: number;
  slowMA: number;
  price: number;
  time: number;
}

export interface RiskDecision {
  approved: boolean;
  signal: Signal;
  reason: string;
  quantity: number;
}

export type ExecutionAction = "BUY" | "SELL" | "SKIP" | "HOLD";

export interface ExecutionResult {
  action: ExecutionAction;
  reason: string;
  price: number;
  quantity: number;
  time: number;
  position: number;
}

export interface PositionState {
  inPosition: boolean;
  entryPrice: number | null;
  entryTime: number | null;
  peakPrice: number | null;
  equityBaseline: number;
}
