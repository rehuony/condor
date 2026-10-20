import { SelectField } from "@/components/ui/select-field";

export interface ChartRangeOption {
  label: string;
  seconds: number;
}

interface ChartRangeToolbarProps {
  intervals: readonly string[];
  interval: string;
  onIntervalChange: (interval: string) => void;
  ranges: readonly ChartRangeOption[];
  rangeSeconds: number;
  /** The chart shows a window no preset names (a measured or dragged range). */
  isCustomRange: boolean;
  onRangeChange: (seconds: number) => void;
  /** What the window control is called on this surface. */
  rangeLabel?: string;
  /** The measured box: placement and size, never the visible chrome. */
  className?: string;
  /**
   * The visible controls' own box. A divider or padding that should sit against
   * the controls goes here, not on `className`, which may be far wider.
   */
  contentClassName?: string;
}

const CUSTOM = "custom";

/**
 * Candle interval and chart window for the CEX and DEX trade workspaces.
 *
 * With room for it every preset shows as a light segmented row; with less, both
 * fold into two compact menus. The switch follows the toolbar's own width (a
 * container query), not the viewport's, so it also folds when it shares a row
 * with the market chip and ticker on a wide screen. Callers size it: as a flex
 * item it needs `flex-1` (or a basis) and a minimum width, because a container
 * cannot take its width from its own contents. The controls hug the end the
 * caller justifies them to, and `contentClassName` styles just their box.
 */
export function ChartRangeToolbar({
  intervals,
  interval,
  onIntervalChange,
  ranges,
  rangeSeconds,
  isCustomRange,
  onRangeChange,
  rangeLabel = "Range",
  className = "",
  contentClassName = "",
}: ChartRangeToolbarProps) {
  const rangeValue = isCustomRange ? CUSTOM : String(rangeSeconds);
  const rangeOptions = [
    ...(isCustomRange ? [{ value: CUSTOM, label: "Custom" }] : []),
    ...ranges.map((r) => ({ value: String(r.seconds), label: r.label })),
  ];
  const pickRange = (value: string) => {
    if (value !== CUSTOM) onRangeChange(Number(value));
  };

  return (
    <div className={`@container flex items-center ${className}`}>
      <div className={`flex items-center self-stretch ${contentClassName}`}>
        {/* One fixed-width segmented control, the height of a market chip, so
            picking a longer label never resizes the bar around it. */}
        <div className="flex h-7 w-32 shrink-0 items-center divide-x divide-[var(--color-border)] rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-px @min-[32rem]:hidden">
          <SelectField size="inline" label="Candle interval" title="Candle interval" className="h-full shrink-0 basis-14"
            value={interval} onChange={onIntervalChange}
            options={intervals.map((iv) => ({ value: iv, label: iv }))} />
          <SelectField size="inline" label="Chart range" title={`Chart ${rangeLabel.toLowerCase()}`} className="h-full flex-1"
            value={rangeValue} onChange={pickRange} options={rangeOptions} />
        </div>

        <div className="hidden items-center gap-3 whitespace-nowrap @min-[32rem]:flex">
          <Segment label="Interval" ariaLabel="Candle interval">
            {intervals.map((iv) => (
              <Pill key={iv} active={interval === iv} onClick={() => onIntervalChange(iv)}>{iv}</Pill>
            ))}
          </Segment>
          <div className="h-4 w-px bg-[var(--color-border)]" />
          <Segment label={rangeLabel} ariaLabel="Chart range">
            {isCustomRange && <span className="px-1 text-[11px] text-[var(--color-primary)]">Custom</span>}
            {ranges.map((r) => (
              <Pill key={r.label} active={!isCustomRange && rangeSeconds === r.seconds}
                onClick={() => onRangeChange(r.seconds)}>{r.label}</Pill>
            ))}
          </Segment>
        </div>
      </div>
    </div>
  );
}

function Segment({ label, ariaLabel, children }: { label: string; ariaLabel: string; children: React.ReactNode }) {
  return (
    <div role="group" aria-label={ariaLabel} className="flex items-center gap-0.5">
      <span className="mr-1 text-[11px] text-[var(--color-text-muted)]">{label}</span>
      {children}
    </div>
  );
}

function Pill({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" onClick={onClick} aria-pressed={active}
      className={`rounded px-1.5 py-0.5 text-[11px] font-medium transition-colors focus-visible:outline-2 focus-visible:outline-[var(--color-primary)] ${
        active
          ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
          : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
      }`}>
      {children}
    </button>
  );
}
