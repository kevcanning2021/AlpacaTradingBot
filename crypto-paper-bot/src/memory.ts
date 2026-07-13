import * as fs from "fs";
import * as path from "path";

export type LedgerAction = "BUY" | "SELL" | "SKIP";
export type LedgerMode = "raw" | "memory";
export type LedgerOutcome = "WIN" | "LOSS" | "";

export interface LedgerRow {
  timestamp: string;
  symbol: string;
  action: LedgerAction;
  price: number;
  quantity: number;
  reason: string;
  mode: LedgerMode;
  outcome: LedgerOutcome;
  pnl: string;
}

const DATA_DIR = path.join(process.cwd(), "data");
const LEDGER_PATH = path.join(DATA_DIR, "ledger.csv");
const LEARNINGS_PATH = path.join(DATA_DIR, "learnings.md");

const LEDGER_HEADER = "timestamp,symbol,action,price,quantity,reason,mode,outcome,pnl";
const LEARNINGS_HEADER = "# Learnings\n\nNo entries yet.\n";

function ensureDataDir(): void {
  fs.mkdirSync(DATA_DIR, { recursive: true });
}

function csvField(value: string | number): string {
  const str = String(value);
  return /[",\n]/.test(str) ? `"${str.replace(/"/g, '""')}"` : str;
}

function parseCsvLine(line: string): string[] {
  const fields: string[] = [];
  let current = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"' && line[i + 1] === '"') {
        current += '"';
        i++;
      } else if (ch === '"') {
        inQuotes = false;
      } else {
        current += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ",") {
      fields.push(current);
      current = "";
    } else {
      current += ch;
    }
  }
  fields.push(current);
  return fields;
}

export function ensureLedgerFile(): void {
  ensureDataDir();
  if (!fs.existsSync(LEDGER_PATH)) {
    fs.writeFileSync(LEDGER_PATH, LEDGER_HEADER + "\n");
  }
}

export function ensureLearningsFile(): void {
  ensureDataDir();
  if (!fs.existsSync(LEARNINGS_PATH)) {
    fs.writeFileSync(LEARNINGS_PATH, LEARNINGS_HEADER);
  }
}

export function appendLedgerRow(row: LedgerRow): void {
  ensureLedgerFile();
  const line = [
    row.timestamp,
    row.symbol,
    row.action,
    row.price,
    row.quantity,
    csvField(row.reason),
    row.mode,
    row.outcome,
    row.pnl,
  ].join(",");
  fs.appendFileSync(LEDGER_PATH, line + "\n");
}

export function readLedger(): LedgerRow[] {
  ensureLedgerFile();
  const lines = fs
    .readFileSync(LEDGER_PATH, "utf-8")
    .trim()
    .split("\n");
  const [, ...dataLines] = lines;

  return dataLines
    .filter((line) => line.trim().length > 0)
    .map(parseCsvLine)
    .map((f) => ({
      timestamp: f[0],
      symbol: f[1],
      action: f[2] as LedgerAction,
      price: Number(f[3]),
      quantity: Number(f[4]),
      reason: f[5],
      mode: f[6] as LedgerMode,
      outcome: f[7] as LedgerOutcome,
      pnl: f[8],
    }));
}

export function appendLearning(text: string): void {
  ensureLearningsFile();
  fs.appendFileSync(LEARNINGS_PATH, `- ${new Date().toISOString()} ${text}\n`);
}

export function readLearnings(): string {
  ensureLearningsFile();
  return fs.readFileSync(LEARNINGS_PATH, "utf-8");
}

export function resetMemory(): void {
  ensureDataDir();
  fs.writeFileSync(LEDGER_PATH, LEDGER_HEADER + "\n");
  fs.writeFileSync(LEARNINGS_PATH, LEARNINGS_HEADER);
}
