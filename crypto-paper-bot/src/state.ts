import * as fs from "fs";
import * as path from "path";
import { PositionState } from "./types";

const STATE_PATH = path.join(process.cwd(), "state.json");

const DEFAULT_STATE: PositionState = {
  inPosition: false,
  entryPrice: null,
  entryTime: null,
  peakPrice: null,
  equityBaseline: 0,
};

export function loadState(): PositionState {
  if (!fs.existsSync(STATE_PATH)) {
    return { ...DEFAULT_STATE };
  }
  return JSON.parse(fs.readFileSync(STATE_PATH, "utf-8")) as PositionState;
}

export function saveState(state: PositionState): void {
  fs.writeFileSync(STATE_PATH, JSON.stringify(state, null, 2));
}
