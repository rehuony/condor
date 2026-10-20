import type { IChartApi, MouseEventParams } from "lightweight-charts";

/** Center the time text without the native label's reserved tick space. */
export function attachTimeAxisLabel(chart: IChartApi, container: HTMLElement): {
  hide: () => void;
  detach: () => void;
} {
  const { layout, crosshair } = chart.options();
  const timeScale = chart.timeScale();
  const height = Math.ceil(layout.fontSize * 1.5 + 6);
  const label = document.createElement("div");
  label.dataset.chartTimeLabel = "";
  Object.assign(label.style, {
    position: "absolute",
    zIndex: "20",
    pointerEvents: "none",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    visibility: "hidden",
    height: `${height}px`,
    padding: `0 ${layout.fontSize * 0.75}px`,
    borderRadius: "0 0 2px 2px",
    background: crosshair.vertLine.labelBackgroundColor,
    color: "white",
    font: `${layout.fontSize}px ${layout.fontFamily}`,
    fontVariantNumeric: "tabular-nums",
    lineHeight: "1",
    whiteSpace: "pre",
    width: "max-content",
  });
  container.append(label);

  // Fixed-length dates and tabular digits keep the width stable. Measure once
  // when the chart becomes visible instead of forcing layout on every move.
  let labelWidth = 0;
  let lastText = "";
  const hide = () => { label.style.visibility = "hidden"; };
  const move = ({ point, time }: MouseEventParams) => {
    const width = timeScale.width();
    if (
      !chart.options().crosshair.vertLine.visible ||
      !point || typeof time !== "number" || !Number.isFinite(time) ||
      width <= 0 || timeScale.height() <= 0 ||
      point.x < 0 || point.x > width ||
      point.y < 0 || point.y >= chart.paneSize().height
    ) {
      hide();
      return;
    }
    const x = timeScale.timeToCoordinate(time);
    if (x === null) {
      hide();
      return;
    }

    const text = new Date(time * 1000).toISOString().slice(0, 16).replace("T", "   ");
    if (text !== lastText) {
      label.textContent = text;
      lastText = text;
    }
    if (labelWidth === 0) labelWidth = label.offsetWidth;
    label.style.left = `${Math.max(0, Math.min(x - labelWidth / 2, width - labelWidth))}px`;
    label.style.bottom = `${timeScale.height() - height}px`;
    label.style.visibility = "visible";
  };

  chart.subscribeCrosshairMove(move);
  timeScale.subscribeSizeChange(hide);
  timeScale.subscribeVisibleLogicalRangeChange(hide);
  return {
    hide,
    detach: () => {
      chart.unsubscribeCrosshairMove(move);
      timeScale.unsubscribeSizeChange(hide);
      timeScale.unsubscribeVisibleLogicalRangeChange(hide);
      label.remove();
    },
  };
}
