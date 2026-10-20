import type { IChartApi, ISeriesApi, LogicalRange, UTCTimestamp } from "lightweight-charts";

/** Restore the same time anchor after prepending, including overlay time points. */
export function preserveChartViewport(chart: IChartApi, anchor: number, change: () => void): void {
  const scale = chart.timeScale();
  const range = scale.getVisibleLogicalRange();
  const before = scale.timeToIndex(anchor as UTCTimestamp);
  change();
  const after = scale.timeToIndex(anchor as UTCTimestamp);
  if (range && before != null && after != null) {
    const shift = after - before;
    scale.setVisibleLogicalRange({ from: range.from + shift, to: range.to + shift });
  }
}

/**
 * Observe the result of actual input, not arbitrary chart range notifications.
 * setData, overlays, resizing and fitContent all move the logical range too.
 * One input frame can request at most one page; a resting cursor buys nothing.
 */
export function attachHistoryNavigation(
  chart: IChartApi,
  series: ISeriesApi<"Candlestick">,
  container: HTMLElement,
  callbacks: {
    onNavigate: () => void;
    onLoadEarlier: () => void;
    canLoadEarlier: () => boolean;
    isDraggingPrice: () => boolean;
  },
): { cancel: () => void; flush: () => void; detach: () => void } {
  const scale = chart.timeScale();
  const pointers = new Map<number, { x: number; y: number }>();
  let frame: number | null = null;
  let before: LogicalRange | null = null;
  const cancel = () => {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
    before = null;
  };
  // Consume actual input before setData or overlays can change its result.
  const flush = () => {
    if (frame === null) return;
    const previous = before;
    cancel();
    const range = scale.getVisibleLogicalRange();
    if (!range || !previous || callbacks.isDraggingPrice()) return;
    if (range.from === previous.from && range.to === previous.to) return;
    callbacks.onNavigate();
    const bars = series.barsInLogicalRange(range);
    if (range.from < previous.from && bars && bars.barsBefore < 50 && callbacks.canLoadEarlier()) {
      callbacks.onLoadEarlier();
    }
  };
  const observe = () => {
    if (frame !== null) return;
    before = scale.getVisibleLogicalRange();
    frame = requestAnimationFrame(flush);
  };
  const down = (event: PointerEvent) => {
    if (event.button === 0 && !event.shiftKey) {
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    }
  };
  const move = (event: PointerEvent) => {
    const origin = pointers.get(event.pointerId);
    if (origin && Math.hypot(event.clientX - origin.x, event.clientY - origin.y) > 4) observe();
  };
  const up = (event: PointerEvent) => { pointers.delete(event.pointerId); };
  const wheel = (event: WheelEvent) => {
    if (event.deltaX || event.deltaY) observe();
  };
  container.addEventListener("pointerdown", down, true);
  container.addEventListener("pointermove", move, true);
  window.addEventListener("pointerup", up);
  window.addEventListener("pointercancel", up);
  container.addEventListener("wheel", wheel, { capture: true, passive: true });
  return {
    cancel,
    flush,
    detach: () => {
      cancel();
      container.removeEventListener("pointerdown", down, true);
      container.removeEventListener("pointermove", move, true);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
      container.removeEventListener("wheel", wheel, true);
    },
  };
}
