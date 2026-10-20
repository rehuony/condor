/** @vitest-environment jsdom */
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("candle history HTTP requests", () => {
  it("carries inclusive zero bounds, a pinned pool, and the cancellation signal", async () => {
    const fetchMock = vi.fn(async () => new Response("[]", { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await api.getCandles("local", "solana/raydium", "SOL-USDC", "5m", 1000, 0, 0, "pool", controller.signal);
    const [url, options] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const params = new URL(url, "http://localhost").searchParams;
    expect(params.get("start_time")).toBe("0");
    expect(params.get("end_time")).toBe("0");
    expect(params.get("pool_address")).toBe("pool");
    expect(options.signal?.aborted).toBe(false);
  });

  it("aborts a stalled history request after 40 seconds", async () => {
    vi.useFakeTimers();
    let requestSignal!: AbortSignal;
    vi.stubGlobal("fetch", vi.fn((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      requestSignal = options.signal!;
      requestSignal.addEventListener("abort", () => reject(requestSignal.reason), { once: true });
    })));
    const request = api.getCandles("local", "binance", "SOL-USDT");
    const rejection = expect(request).rejects.toMatchObject({
      name: "TimeoutError", message: "Candle history request timed out. Please retry.",
    });
    await vi.advanceTimersByTimeAsync(39_999);
    expect(requestSignal.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    await rejection;
    expect(requestSignal.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("keeps the deadline active while the response body is pending", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn(async (_url: string, options: RequestInit) => ({
      ok: true,
      json: () => new Promise((_resolve, reject) => {
        options.signal!.addEventListener("abort", () => reject(options.signal!.reason), { once: true });
      }),
    })));
    const rejection = expect(api.getCandles("local", "binance", "SOL-USDT")).rejects.toMatchObject({
      name: "TimeoutError",
    });
    await vi.advanceTimersByTimeAsync(40_000);
    await rejection;
    expect(vi.getTimerCount()).toBe(0);
  });

  it("forwards cancellation immediately and clears the pending deadline", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    let requestSignal!: AbortSignal;
    vi.stubGlobal("fetch", vi.fn((_url: string, options: RequestInit) => new Promise((_resolve, reject) => {
      requestSignal = options.signal!;
      requestSignal.addEventListener("abort", () => reject(requestSignal.reason), { once: true });
    })));
    const request = api.getCandles("local", "binance", "SOL-USDT", "5m", 1000, 0, 0, undefined, controller.signal);
    controller.abort();
    await expect(request).rejects.toBe(controller.signal.reason);
    expect(requestSignal.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("does not dispatch an already cancelled history request", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    controller.abort();
    await expect(api.getCandles("local", "binance", "SOL-USDT", "5m", 1000, 0, 0, undefined, controller.signal))
      .rejects.toBe(controller.signal.reason);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([200, 503])("clears the deadline after HTTP %s and detaches cancellation", async (status) => {
    vi.useFakeTimers();
    const fetchMock = vi.fn(async () => new Response(status === 200 ? "[]" : "{}", { status }));
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    const request = api.getCandles("local", "binance", "SOL-USDT", "5m", 1000, 0, 0, undefined, controller.signal);
    if (status === 200) await expect(request).resolves.toEqual([]);
    else await expect(request).rejects.toMatchObject({ status });
    expect(vi.getTimerCount()).toBe(0);
    controller.abort();
    const [, options] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(options.signal?.aborted).toBe(false);
  });

  it.each([
    ["12", 12],
    ["0", 0],
    ["Fri, 16 Oct 2026 16:20:15 GMT", 15],
    ["Fri, 16 Oct 2026 16:19:59 GMT", 0],
    ["invalid", undefined],
    [null, undefined],
  ])("decodes Retry-After %s as seconds", async (header, expected) => {
    vi.useFakeTimers();
    vi.setSystemTime(Date.parse("2026-10-16T16:20:00Z"));
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail: "Rate limited" }), {
      status: 429,
      headers: header === null ? {} : { "Retry-After": header },
    })));
    await expect(api.getCandles("local", "binance", "SOL-USDT")).rejects.toMatchObject({
      message: "Rate limited", status: 429, retryAfter: expected,
    });
  });
});
