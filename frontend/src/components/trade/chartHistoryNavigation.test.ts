/** @vitest-environment jsdom */

import type { IChartApi, ISeriesApi, LogicalRange } from "lightweight-charts";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  attachHistoryNavigation,
  preserveChartViewport,
} from "@/components/trade/chartHistoryNavigation";

let container: HTMLDivElement;
let visibleRange: LogicalRange | null;
let navigation: ReturnType<typeof attachHistoryNavigation>;
const frames = new Map<number, FrameRequestCallback>();
const scale = {
  getVisibleLogicalRange: vi.fn(() => visibleRange),
  timeToIndex: vi.fn<() => number | null>(),
  setVisibleLogicalRange: vi.fn(),
};
const chart = { timeScale: () => scale } as unknown as IChartApi;
const series = { barsInLogicalRange: vi.fn() };
const callbacks = {
  onNavigate: vi.fn(),
  onLoadEarlier: vi.fn(),
  canLoadEarlier: vi.fn(() => true),
  isDraggingPrice: vi.fn(() => false),
};

function setRange(from: number, to: number): void {
  visibleRange = { from, to } as LogicalRange;
}

function flushFrame(): void {
  const pending = [...frames.values()];
  frames.clear();
  for (const callback of pending) callback(0);
}

function wheel(deltaY = -100): void {
  container.dispatchEvent(new WheelEvent("wheel", { bubbles: true, deltaY }));
}

function pointer(type: string, x: number, options: MouseEventInit = {}): void {
  // jsdom has no PointerEvent constructor; the helper only needs these fields.
  const event = new MouseEvent(type, { bubbles: true, clientX: x, clientY: 20, ...options });
  Object.defineProperty(event, "pointerId", { value: 1 });
  container.dispatchEvent(event);
}

beforeEach(() => {
  vi.resetAllMocks();
  frames.clear();
  let nextFrame = 0;
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
    const id = ++nextFrame;
    frames.set(id, callback);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => { frames.delete(id); });
  setRange(60, 160);
  scale.getVisibleLogicalRange.mockImplementation(() => visibleRange);
  series.barsInLogicalRange.mockReturnValue({ barsBefore: 25, barsAfter: 100 });
  callbacks.canLoadEarlier.mockReturnValue(true);
  callbacks.isDraggingPrice.mockReturnValue(false);
  container = document.createElement("div");
  document.body.append(container);
  navigation = attachHistoryNavigation(
    chart,
    series as unknown as ISeriesApi<"Candlestick">,
    container,
    callbacks,
  );
});

afterEach(() => {
  navigation.detach();
  container.remove();
  vi.unstubAllGlobals();
});

describe("chart history navigation", () => {
  it("does not load or mark a custom range after programmatic range changes", () => {
    // fitContent, a resize, or a store prepend can all move the logical range.
    setRange(-10, 200);
    flushFrame();

    expect(callbacks.onNavigate).not.toHaveBeenCalled();
    expect(callbacks.onLoadEarlier).not.toHaveBeenCalled();
  });

  it("only observes wheel input when that input actually changes the range", () => {
    wheel();
    flushFrame();
    expect(callbacks.onNavigate).not.toHaveBeenCalled();

    wheel();
    setRange(40, 140);
    flushFrame();

    expect(callbacks.onNavigate).toHaveBeenCalledTimes(1);
    expect(callbacks.onLoadEarlier).toHaveBeenCalledTimes(1);
  });

  it("coalesces input into one page per frame and needs fresh input for another", () => {
    wheel();
    setRange(40, 140);
    wheel();
    setRange(20, 120);
    wheel();
    flushFrame();
    expect(callbacks.onLoadEarlier).toHaveBeenCalledTimes(1);

    setRange(-20, 80);
    flushFrame();
    expect(callbacks.onLoadEarlier).toHaveBeenCalledTimes(1);

    wheel();
    setRange(-40, 60);
    flushFrame();
    expect(callbacks.onLoadEarlier).toHaveBeenCalledTimes(2);
  });

  it("flushes pending movement once before programmatic data changes", () => {
    wheel();
    setRange(40, 140);
    navigation.flush();

    expect(callbacks.onNavigate).toHaveBeenCalledTimes(1);
    expect(callbacks.onLoadEarlier).toHaveBeenCalledTimes(1);
    expect(frames.size).toBe(0);

    setRange(-20, 200); // the subsequent setData/fit is not another input
    navigation.flush();
    flushFrame();
    expect(callbacks.onNavigate).toHaveBeenCalledTimes(1);
    expect(callbacks.onLoadEarlier).toHaveBeenCalledTimes(1);
  });

  it("loads after a primary pan and stops tracking that pointer after release", () => {
    pointer("pointerdown", 100);
    pointer("pointermove", 103);
    expect(frames.size).toBe(0);

    pointer("pointermove", 110);
    setRange(30, 130);
    flushFrame();
    expect(callbacks.onNavigate).toHaveBeenCalledTimes(1);
    expect(callbacks.onLoadEarlier).toHaveBeenCalledTimes(1);

    pointer("pointerup", 110);
    pointer("pointermove", 140);
    setRange(0, 100);
    flushFrame();
    expect(callbacks.onLoadEarlier).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["Shift measurement", { shiftKey: true }],
    ["secondary button", { button: 2 }],
  ])("does not treat %s as a pan", (_name, options) => {
    pointer("pointerdown", 100, options);
    pointer("pointermove", 130, options);
    setRange(20, 120);
    flushFrame();

    expect(callbacks.onNavigate).not.toHaveBeenCalled();
    expect(callbacks.onLoadEarlier).not.toHaveBeenCalled();
  });

  it.each([
    ["away from the left boundary", 50, true, 40],
    ["while loading or exhausted", 25, false, 40],
    ["moving right", 25, true, 80],
  ])("marks navigation but does not load %s", (_name, barsBefore, canLoad, from) => {
    series.barsInLogicalRange.mockReturnValue({ barsBefore, barsAfter: 100 });
    callbacks.canLoadEarlier.mockReturnValue(canLoad);
    wheel();
    setRange(from, from + 100);
    flushFrame();

    expect(callbacks.onNavigate).toHaveBeenCalledTimes(1);
    expect(callbacks.onLoadEarlier).not.toHaveBeenCalled();
  });

  it("ignores range changes while dragging a price line", () => {
    pointer("pointerdown", 100);
    pointer("pointermove", 130);
    callbacks.isDraggingPrice.mockReturnValue(true);
    setRange(20, 120);
    flushFrame();

    expect(callbacks.onNavigate).not.toHaveBeenCalled();
    expect(callbacks.onLoadEarlier).not.toHaveBeenCalled();
  });

  it("cancels an old input frame when a preset is reset but accepts later input", () => {
    wheel();
    navigation.cancel();
    setRange(0, 200);
    flushFrame();
    expect(callbacks.onNavigate).not.toHaveBeenCalled();

    wheel();
    setRange(-20, 180);
    flushFrame();
    expect(callbacks.onLoadEarlier).toHaveBeenCalledTimes(1);
  });

  it("removes listeners and pending animation work when the chart is detached", () => {
    wheel();
    expect(frames.size).toBe(1);
    navigation.detach();
    expect(frames.size).toBe(0);

    wheel();
    pointer("pointerdown", 100);
    pointer("pointermove", 130);
    setRange(0, 100);
    flushFrame();
    expect(callbacks.onNavigate).not.toHaveBeenCalled();
    expect(callbacks.onLoadEarlier).not.toHaveBeenCalled();
    expect(frames.size).toBe(0);
  });
});

describe("preserveChartViewport", () => {
  it("keeps the same time anchor and fractional zoom span when overlays add indices", () => {
    setRange(-1.5, 15.75);
    let anchorIndex = 3;
    scale.timeToIndex.mockImplementation(() => anchorIndex);

    preserveChartViewport(chart, 1_700_000_000, () => {
      // Twenty older candles plus four distinct overlay times shift this
      // anchor by 24, so counting just the candles would move the viewport.
      anchorIndex += 24;
      setRange(0, 100); // the library's intermediate range is not the user's view
    });

    expect(scale.setVisibleLogicalRange).toHaveBeenCalledExactlyOnceWith({
      from: 22.5,
      to: 39.75,
    });
    expect(scale.timeToIndex).toHaveBeenNthCalledWith(1, 1_700_000_000);
    expect(scale.timeToIndex).toHaveBeenNthCalledWith(2, 1_700_000_000);
  });

  it("still applies new data when the old time anchor is unavailable", () => {
    scale.timeToIndex.mockReturnValueOnce(null).mockReturnValueOnce(20);
    const change = vi.fn();

    preserveChartViewport(chart, 1_700_000_000, change);

    expect(change).toHaveBeenCalledTimes(1);
    expect(scale.setVisibleLogicalRange).not.toHaveBeenCalled();
  });
});
