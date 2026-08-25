import * as path from "path";
import * as dotenv from "dotenv";
import { BotConfig } from "./types";

dotenv.config({ path: path.join(process.cwd(), ".env"), quiet: true });

function envString(name: string, fallback: string): string {
  const raw = process.env[name];
  return raw === undefined || raw.trim() === "" ? fallback : raw;
}

function envNumber(name: string, fallback: number): number {
  const raw = process.env[name];
  if (raw === undefined || raw.trim() === "") return fallback;
  const parsed = Number(raw);
  if (Number.isNaN(parsed)) {
    throw new Error(`Environment variable ${name}="${raw}" is not a valid number.`);
  }
  return parsed;
}

// Single source of truth for symbol/interval/strategy/sizing settings —
// see .env.example for every knob. No broker credentials belong here:
// this bot has no order-placing code path at all.
export function getConfig(): BotConfig {
  return {
    symbol: envString("SYMBOL", "BTCUSDT"),
    interval: envString("INTERVAL", "5m"),
    fastLength: envNumber("FAST_LENGTH", 9),
    slowLength: envNumber("SLOW_LENGTH", 21),
    quantity: envNumber("QUANTITY", 1),
    maxPosition: envNumber("MAX_POSITION", 1),
  };
}
