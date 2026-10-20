/** Inclusive Unix-second windows shared by historical requests and their cache. */
export interface CandleHistoryRange {
  from: number;
  to: number;
}

export const HISTORY_PAGE_CANDLES = 1000;
export const MAX_HISTORY_PAGE_SECONDS = 180 * 24 * 60 * 60;

export function intervalSeconds(interval: string): number {
  const match = /^(\d+)(s|m|h|d|w)$/.exec(interval);
  if (!match || Number(match[1]) === 0) throw new Error(`Unsupported candle interval: ${interval}`);
  const units: Record<string, number> = { s: 1, m: 60, h: 3600, d: 86400, w: 604800 };
  return Number(match[1]) * units[match[2]];
}

/** End each page immediately before the next page starts, including sparse data. */
export function historyPage(to: number, interval: string, from = 0): CandleHistoryRange {
  const step = intervalSeconds(interval);
  const periods = Math.min(HISTORY_PAGE_CANDLES, Math.floor(MAX_HISTORY_PAGE_SECONDS / step));
  // Align to candle boundaries so a non-aligned `now` cannot include 1,001 bars.
  const pageFrom = Math.floor(to / step) * step - (Math.max(1, periods) - 1) * step;
  return { from: Math.max(from, pageFrom), to };
}

export function mergeHistoryRanges(
  ranges: CandleHistoryRange[],
  added: CandleHistoryRange,
): CandleHistoryRange[] {
  const merged: CandleHistoryRange[] = [];
  for (const range of [...ranges, added].sort((a, b) => a.from - b.from)) {
    const last = merged.at(-1);
    if (last && range.from <= last.to + 1) last.to = Math.max(last.to, range.to);
    else merged.push({ ...range });
  }
  return merged;
}

/** Return the newest uncached portion of a requested window. */
export function missingHistoryRange(
  ranges: CandleHistoryRange[],
  requested: CandleHistoryRange,
): CandleHistoryRange | null {
  let to = requested.to;
  for (let i = ranges.length - 1; i >= 0; i--) {
    const covered = ranges[i];
    if (covered.from > to) continue;
    if (covered.to < requested.from) break;
    if (covered.to < to) return { from: Math.max(requested.from, covered.to + 1), to };
    to = covered.from - 1;
    if (to < requested.from) return null;
  }
  return { from: requested.from, to };
}

export function waitForHistoryRetry(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}
