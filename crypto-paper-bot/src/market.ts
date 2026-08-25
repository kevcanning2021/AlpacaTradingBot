import { Candle } from "./types";

const BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines";

/**
 * Fetches real historical candles from Binance's public klines endpoint.
 * No API key required — this is a public market-data endpoint.
 * Throws on any network/HTTP failure rather than returning an empty
 * array, so a fetch problem is never silently mistaken for "no data".
 */
export async function getKlines(
  symbol: string,
  interval: string,
  limit: number
): Promise<Candle[]> {
  const url = `${BINANCE_KLINES_URL}?symbol=${encodeURIComponent(
    symbol
  )}&interval=${encodeURIComponent(interval)}&limit=${limit}`;

  let response: Response;
  try {
    response = await fetch(url);
  } catch (err) {
    throw new Error(
      `Failed to reach Binance klines endpoint for ${symbol} ${interval}: ${
        err instanceof Error ? err.message : String(err)
      }`
    );
  }

  if (!response.ok) {
    const body = await response.text().catch(() => "<no body>");
    throw new Error(
      `Binance klines request failed (${response.status} ${response.statusText}) for ${symbol} ${interval}: ${body}`
    );
  }

  const raw = (await response.json()) as unknown[];

  if (!Array.isArray(raw) || raw.length === 0) {
    throw new Error(
      `Binance returned no candle data for ${symbol} ${interval}.`
    );
  }

  return raw.map((row) => {
    const r = row as [
      number,
      string,
      string,
      string,
      string,
      string,
      number,
      ...unknown[]
    ];
    return {
      openTime: r[0],
      open: parseFloat(r[1]),
      high: parseFloat(r[2]),
      low: parseFloat(r[3]),
      close: parseFloat(r[4]),
      volume: parseFloat(r[5]),
      closeTime: r[6],
    };
  });
}
