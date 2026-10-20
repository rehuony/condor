import { useCallback, useEffect, useRef, useState } from "react";

import { api, errorStatus, type CandleData } from "@/lib/api";
import { candleChannelKey, candleStore } from "@/lib/candle-store";
import {
  historyPage,
  waitForHistoryRetry,
  type CandleHistoryRange,
} from "@/lib/candle-history";

export interface CandleHistoryOptions {
  server: string | null;
  connector: string;
  pair: string;
  interval: string;
  poolAddress?: string;
  lookbackSeconds: number;
  rangeRequestId?: number;
}

export interface CandleHistoryState {
  loadedFrom: number | null;
  loadedTo: number | null;
  status: "loading" | "waiting" | "ready" | "empty" | "error";
  initialComplete: boolean;
  error: string | null;
  /** Epoch milliseconds, for displaying the current Retry-After wait. */
  retryAt: number | null;
}

interface HistoryActions {
  loadEarlier: () => void;
  retry: () => void;
}

const INITIAL_STATE: CandleHistoryState = {
  loadedFrom: null,
  loadedTo: null,
  status: "loading",
  initialComplete: false,
  error: null,
  retryAt: null,
};

/**
 * Fetch the preset window in bounded pages, then one earlier window per action.
 * The caller's useCandleStore subscription retains history while this chart lives.
 * Every request and retry belongs to one effect, so obsolete completions cannot
 * populate a different market or resurrect a cancelled range request.
 */
export function useCandleHistory({
  server,
  connector,
  pair,
  interval,
  poolAddress,
  lookbackSeconds,
  rangeRequestId = 0,
}: CandleHistoryOptions): CandleHistoryState & HistoryActions {
  const key = server ? candleChannelKey(server, connector, pair, interval, poolAddress) : "";
  const identity = JSON.stringify([key, lookbackSeconds, rangeRequestId]);
  const [snapshot, setSnapshot] = useState<{ identity: string; state: CandleHistoryState }>(
    () => ({ identity: "", state: INITIAL_STATE }),
  );
  const actionsRef = useRef<HistoryActions | null>(null);

  useEffect(() => {
    if (!server || !key) return;
    const controller = new AbortController();
    const { signal } = controller;
    const to = Math.floor(Date.now() / 1000);
    const from = Math.max(0, to - lookbackSeconds);
    let state: CandleHistoryState = { ...INITIAL_STATE, loadedFrom: from, loadedTo: to };
    let busy = false;
    let failed: { range: CandleHistoryRange; initial: boolean } | null = null;

    const publish = (changes: Partial<CandleHistoryState>) => {
      if (signal.aborted) return;
      state = { ...state, ...changes };
      setSnapshot({ identity, state });
    };
    publish({});

    async function fetchPage(range: CandleHistoryRange): Promise<CandleData[]> {
      let failures = 0;
      while (!signal.aborted) {
        try {
          publish({ status: "loading", error: null, retryAt: null });
          const rows = await api.getCandles(
            server!, connector, pair, interval, 1000, range.from, range.to, poolAddress, signal,
          );
          if (signal.aborted) throw new DOMException("Aborted", "AbortError");
          // Never cache a provider's fallback/recent rows under an older window.
          return rows.filter((row) => {
            const timestamp = row.timestamp > 1e12 ? row.timestamp / 1000 : row.timestamp;
            return timestamp >= range.from && timestamp <= range.to;
          });
        } catch (error) {
          if (signal.aborted) throw error;
          let delay: number;
          if (errorStatus(error) === 429) {
            const supplied = (error as { retryAfter?: number }).retryAfter;
            delay = Number.isFinite(supplied) ? Math.max(0, supplied!) * 1000 : 60_000;
            publish({ status: "waiting", retryAt: Date.now() + delay });
          } else {
            if (failures >= 2) throw error;
            delay = ++failures * 2000;
          }
          await waitForHistoryRetry(delay, signal);
        }
      }
      throw new DOMException("Aborted", "AbortError");
    }

    async function load(range: CandleHistoryRange, initial: boolean) {
      if (busy || signal.aborted) return;
      busy = true;
      failed = { range, initial };
      publish({ status: "loading", error: null, retryAt: null });
      try {
        let missing = candleStore.missingHistory(key, range);
        while (missing && !signal.aborted) {
          const page = historyPage(missing.to, interval, missing.from);
          const rows = await fetchPage(page);
          if (signal.aborted) return;
          candleStore.mergeHistory(key, rows, page);
          // Always advance on the successful requested boundary, even if the
          // store no longer retains this chart. Cached coverage can skip other
          // windows, but is not what makes a successful walk progress.
          const cursor = page.from - 1;
          missing = cursor < range.from
            ? null
            : candleStore.missingHistory(key, { from: range.from, to: cursor });
        }
        if (signal.aborted) return;
        const hasCandles = candleStore.getCandles(key).some(
          (row) => row.timestamp >= range.from && row.timestamp <= range.to,
        );
        failed = null;
        publish({
          loadedFrom: initial ? from : range.from,
          initialComplete: true,
          status: hasCandles ? "ready" : "empty",
          retryAt: null,
        });
      } catch (error) {
        if (!signal.aborted) {
          publish({
            status: "error",
            error: error instanceof Error ? error.message : "Unable to load candle history",
            retryAt: null,
          });
        }
      } finally {
        busy = false;
      }
    }

    actionsRef.current = {
      loadEarlier: () => {
        if (busy || failed || !state.initialComplete || state.loadedFrom === null) return;
        if (state.loadedFrom <= 0) {
          publish({ status: "empty" });
          return;
        }
        void load(historyPage(state.loadedFrom - 1, interval), false);
      },
      retry: () => {
        if (!busy && failed) void load(failed.range, failed.initial);
      },
    };
    void load({ from, to }, true);
    return () => {
      actionsRef.current = null;
      controller.abort();
    };
  }, [server, connector, pair, interval, poolAddress, lookbackSeconds, rangeRequestId, key, identity]);

  const loadEarlier = useCallback(() => actionsRef.current?.loadEarlier(), []);
  const retry = useCallback(() => actionsRef.current?.retry(), []);
  // A key/range change must not expose the previous market's window for one render.
  const visible = snapshot.identity === identity ? snapshot.state : INITIAL_STATE;
  return { ...visible, loadEarlier, retry };
}
