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
  className?: string;
}

const CUSTOM = "custom";

/**
 * Candle interval and chart window for the CEX and DEX trade workspaces.
 *
 * Wide layouts show every preset as a light segmented row; narrow layouts fold
 * both into two compact menus so the chart keeps its height.
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
    <div className={`flex items-center gap-2 ${className}`}>
      {/* One fixed-width segmented control, the height of a market chip, so
          picking a longer label never resizes the bar around it. */}
      <div className="flex h-7 w-36 items-center divide-x divide-[var(--color-border)] rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-px md:hidden">
        <SelectField size="inline" label="Candle interval" title="Candle interval" className="h-full shrink-0 basis-14"
          value={interval} onChange={onIntervalChange}
          options={intervals.map((iv) => ({ value: iv, label: iv }))} />
        <SelectField size="inline" label="Chart range" title={`Chart ${rangeLabel.toLowerCase()}`} className="h-full flex-1"
          value={rangeValue} onChange={pickRange} options={rangeOptions} />
      </div>

      <div className="hidden items-center gap-3 md:flex">
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
