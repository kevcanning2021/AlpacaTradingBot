import * as fs from "fs";
import * as path from "path";

const LOG_PATH = path.join(process.cwd(), "lessons.log");

export function logEvent(message: string): void {
  fs.appendFileSync(LOG_PATH, `[${new Date().toISOString()}] ${message}\n`);
}
