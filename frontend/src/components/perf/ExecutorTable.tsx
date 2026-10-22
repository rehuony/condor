import {
  ChevronDown,
  ChevronUp,
  Circle,
  Square,
  Trash2,
  TrendingUp,
  X,
} from "lucide-react";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { ExecutorChart } from "@/components/charts/ExecutorChart";
import { PairLabel } from "@/components/executor/PairLabel";
import { Card } from "@/components/ui/Card";
import { IconButton } from "@/components/ui/IconButton";
import { useResizeDrag } from "@/hooks/useResizeDrag";
import type { ExecutorInfo } from "@/lib/api/executors";
import {
  formatUsd,
  formatVolume,
  formatPnl,
  pnlColor,
  formatAge,
  formatPrice,
  formatPct,
  formatConnectorName,
  isExecutorActive,
  isExecutorTerminated,
} from "@/lib/formatters";

// ── Sort types ──

export type SortKey =
  | "id"
  | "type"
  | "connector"
  | "trading_pair"
  | "side"
  | "pnl"
  | "net_pnl_pct"
  | "volume"
  | "cum_fees_quote"
  | "status"
  | "close_type"
  | "timestamp";

export type SortDir = "asc" | "desc";

function compareExecutors(a: ExecutorInfo, b: ExecutorInfo, key: SortKey, dir: SortDir): number {
  let cmp = 0;
  switch (key) {
    case "id":
    case "type":
    case "connector":
    case "trading_pair":
    case "side":
    case "status":
    case "close_type":
      cmp = (a[key] || "").localeCompare(b[key] || "");
      break;
    case "pnl":
    case "net_pnl_pct":
    case "volume":
    case "cum_fees_quote":
    case "timestamp":
      cmp = (a[key] || 0) - (b[key] || 0);
      break;
  }
  return dir === "asc" ? cmp : -cmp;
}

// ── Status Dot ──

export function StatusDot({ status }: { status: string }) {
  const color =
    isExecutorActive(status)
      ? "text-[var(--color-green)]"
      : status === "failed" || status === "error"
        ? "text-[var(--color-red)]"
        : "text-[var(--color-text-muted)]";
  return <Circle className={`h-2 w-2 fill-current ${color}`} />;
}

// ── Sortable Header ──

/** One cell's box, shared by header and body so every column lines up. */
const CELL = "whitespace-nowrap px-2 text-center";

/**
 * A column header that sorts. A real button, so it is reachable by keyboard;
 * the arrow keeps its slot when inactive and a matching spacer sits before a
 * centred label, so the label stays over the column's centre.
 */
export function SortHeader({
  label,
  sortKey,
  currentKey,
  currentDir,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  currentKey: SortKey;
  currentDir: SortDir;
  onSort: (key: SortKey) => void;
}) {
  const active = currentKey === sortKey;
  const Arrow = currentDir === "asc" ? ChevronUp : ChevronDown;
  return (
    <th
      scope="col"
      aria-sort={active ? (currentDir === "asc" ? "ascending" : "descending") : "none"}
      className={`${CELL} h-10 font-medium`}
    >
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className={`inline-flex items-center gap-1 rounded uppercase tracking-wider transition-colors hover:text-[var(--color-text)] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-primary)] ${
          active ? "text-[var(--color-text)]" : ""
        }`}
      >
        <span className="w-3" aria-hidden="true" />
        {label}
        <Arrow className={`h-3 w-3 ${active ? "" : "invisible"}`} aria-hidden="true" />
      </button>
    </th>
  );
}

/** The header checkbox: checked when every row is, mixed when only some are. */
function SelectAllCheckbox({
  checked,
  mixed,
  onChange,
}: {
  checked: boolean;
  mixed: boolean;
  onChange?: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = mixed;
  }, [mixed]);
  return (
    <input
      ref={ref}
      type="checkbox"
      aria-label="Select all executors"
      checked={checked}
      onChange={onChange}
      className="rounded border-[var(--color-border)]"
    />
  );
}

// ── Executor Row (memoized) ──

type RowFormatter = (val: number, quote: string) => string;

const ExecutorRow = memo(function ExecutorRow({
  ex,
  isSelected,
  isChecked,
  isStopping,
  isDeleting,
  onRowClick,
  onToggleSelect,
  onStop,
  onDelete,
  fmtPnl,
  fmtVol,
  fmtDet,
}: {
  ex: ExecutorInfo;
  isSelected: boolean;
  /** Only meaningful when `onToggleSelect` is given. */
  isChecked?: boolean;
  isStopping: boolean;
  isDeleting: boolean;
  onRowClick: (ex: ExecutorInfo) => void;
  /** Omitted by hosts with no bulk action: no checkbox cell is drawn without it. */
  onToggleSelect?: (id: string) => void;
  /** Omitted by read-only hosts: no Stop control is drawn without it. */
  onStop?: (id: string) => void;
  onDelete?: (id: string) => void;
  fmtPnl: RowFormatter;
  fmtVol: RowFormatter;
  fmtDet: RowFormatter;
}) {
  const side = ex.side.toUpperCase();
  const quote = ex.trading_pair?.split("-")[1] || "USDT";
  // A checked row is tinted, the row whose detail is open is highlighted;
  // neither needs the coloured edge rows used to carry.
  const tone = isChecked
    ? "bg-[var(--color-primary)]/[0.06]"
    : isSelected
      ? "bg-[var(--color-surface-hover)]"
      : "hover:bg-[var(--color-surface-hover)]/60";
  return (
    <tr
      className={`h-11 cursor-pointer border-b border-[var(--color-border)]/50 transition-colors last:border-0 ${tone}`}
      onClick={() => onRowClick(ex)}
    >
      {onToggleSelect && (
        <td className="w-10 pl-3 pr-1 text-center" onClick={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            aria-label={`Select executor ${ex.id.slice(0, 8)}`}
            checked={isChecked ?? false}
            onChange={() => onToggleSelect(ex.id)}
            className="rounded border-[var(--color-border)]"
          />
        </td>
      )}
      <td className={`${CELL} font-mono text-xs text-[var(--color-text-muted)]`} title={ex.id}>
        {ex.id.slice(0, 8)}
      </td>
      <td className={CELL}>
        <span className="rounded border border-[var(--color-border)]/60 px-1.5 py-0.5 text-[11px] font-medium">
          {ex.type}
        </span>
      </td>
      <td className={`${CELL} text-[var(--color-text-muted)]`} title={ex.connector}>
        {formatConnectorName(ex.connector)}
      </td>
      <td className={`${CELL} font-medium`}>
        <PairLabel tradingPair={ex.trading_pair} connector={ex.connector} />
      </td>
      <td className={`${CELL} text-xs font-semibold`} style={{ color: side === "BUY" ? "var(--color-green)" : "var(--color-red)" }}>
        {side}
      </td>
      <td className={`${CELL} tabular-nums`}>
        <span className="font-medium" style={{ color: pnlColor(ex.pnl) }}>{fmtPnl(ex.pnl, quote)}</span>
        <span className="ml-1.5 text-xs" style={{ color: ex.net_pnl_pct ? pnlColor(ex.net_pnl_pct) : undefined }}>
          {formatPct(ex.net_pnl_pct)}
        </span>
      </td>
      <td className={`${CELL} tabular-nums text-[var(--color-text-muted)]`}>{fmtVol(ex.volume, quote)}</td>
      <td className={`${CELL} tabular-nums text-[var(--color-text-muted)]`}>
        {ex.cum_fees_quote ? fmtDet(ex.cum_fees_quote, quote) : "—"}
      </td>
      <td className={`${CELL} text-[var(--color-text-muted)]`}>{ex.close_type || "—"}</td>
      <td className={`${CELL} tabular-nums text-[var(--color-text-muted)]`}>{formatAge(ex.timestamp)}</td>
      <td className="w-10 px-2 text-center" onClick={(e) => e.stopPropagation()}>
        {onStop && isExecutorActive(ex.status) && (
          <IconButton
            label="Stop executor"
            size="sm"
            tone="danger"
            onClick={() => onStop(ex.id)}
            disabled={isStopping}
          >
            <Square className="h-3.5 w-3.5" />
          </IconButton>
        )}
        {onDelete && isExecutorTerminated(ex.status) && (
          <IconButton
            label="Delete executor history"
            size="sm"
            tone="danger"
            onClick={() => onDelete(ex.id)}
            disabled={isDeleting}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </IconButton>
        )}
      </td>
    </tr>
  );
});

// ── Executor Table ──

// Cap how many rows mount at once so large history sets (up to ~2000) don't
// render ~26k DOM cells or re-reconcile every row on each sort click.
const INITIAL_VISIBLE = 100;
const VISIBLE_STEP = 100;

/**
 * Executors as one table: single-line cells centred under their headers, a
 * muted header row and no frame of its own, so a host's card is the only
 * border. When it is wider than its host it scrolls sideways without drawing
 * a scrollbar.
 */
export function ExecutorTable({
  executors,
  sortKey,
  sortDir,
  onSort,
  selectedIds,
  onToggleSelect,
  onSelectAll,
  allSelected,
  onRowClick,
  selectedExecutorId,
  onStop,
  stoppingIds,
  onDelete,
  deletingIds,
  rateFormatPnl,
  rateFormatValue,
  rateFormatDetailed,
}: {
  executors: ExecutorInfo[];
  sortKey: SortKey;
  sortDir: SortDir;
  onSort: (key: SortKey) => void;
  /** The four selection props travel together: a host that wants the checkbox
   *  column passes all of them, a read-only host passes none and the column is
   *  not drawn at all. */
  selectedIds?: Set<string>;
  onToggleSelect?: (id: string) => void;
  onSelectAll?: () => void;
  allSelected?: boolean;
  onRowClick: (ex: ExecutorInfo) => void;
  selectedExecutorId: string | null;
  /** Omit for a read-only table: rows then draw no Stop control. */
  onStop?: (id: string) => void;
  stoppingIds?: Set<string>;
  /** History deletion is only available in hosts that explicitly opt in. */
  onDelete?: (id: string) => void;
  deletingIds?: Set<string>;
  rateFormatPnl?: (val: number, quote: string) => string;
  rateFormatValue?: (val: number, quote: string) => string;
  rateFormatDetailed?: (val: number, quote: string) => string;
}) {
  const sorted = useMemo(
    () => [...executors].sort((a, b) => compareExecutors(a, b, sortKey, sortDir)),
    [executors, sortKey, sortDir],
  );

  // Incremental reveal: sort on the full array (so top-N by any column stays
  // correct) but only mount the first `visibleCount` rows.
  const [visibleCount, setVisibleCount] = useState(INITIAL_VISIBLE);
  const visible = useMemo(() => sorted.slice(0, visibleCount), [sorted, visibleCount]);
  const remaining = sorted.length - visible.length;

  const fmtPnl = useMemo<RowFormatter>(
    () => rateFormatPnl ?? ((val: number) => formatPnl(val)),
    [rateFormatPnl],
  );
  const fmtVol = useMemo<RowFormatter>(
    () => rateFormatValue ?? ((val: number) => formatVolume(val)),
    [rateFormatValue],
  );
  const fmtDet = useMemo<RowFormatter>(
    () => rateFormatDetailed ?? ((val: number) => formatUsd(val)),
    [rateFormatDetailed],
  );
  const someSelected = executors.some((ex) => selectedIds?.has(ex.id));
  const header = (label: string, key: SortKey) => (
    <SortHeader label={label} sortKey={key} currentKey={sortKey} currentDir={sortDir} onSort={onSort} />
  );

  return (
    <div>
      <div className="scrollbar-none overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-[var(--color-border)] bg-[var(--color-bg)]/60 text-[11px] text-[var(--color-text-muted)]">
              {onToggleSelect && (
                <th scope="col" className="h-10 w-10 pl-3 pr-1 text-center">
                  <SelectAllCheckbox
                    checked={(allSelected ?? false) && executors.length > 0}
                    mixed={someSelected && !allSelected}
                    onChange={onSelectAll}
                  />
                </th>
              )}
              {header("ID", "id")}
              {header("Type", "type")}
              {header("Venue", "connector")}
              {header("Pair", "trading_pair")}
              {header("Side", "side")}
              {header("PnL", "pnl")}
              {header("Volume", "volume")}
              {header("Fees", "cum_fees_quote")}
              {header("Close", "close_type")}
              {header("Age", "timestamp")}
              <th scope="col" className="h-10 w-10 px-2">
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {visible.map((ex) => (
              <ExecutorRow
                key={ex.id}
                ex={ex}
                isSelected={selectedExecutorId === ex.id}
                isChecked={selectedIds?.has(ex.id) ?? false}
                isStopping={stoppingIds?.has(ex.id) ?? false}
                isDeleting={deletingIds?.has(ex.id) ?? false}
                onRowClick={onRowClick}
                onToggleSelect={onToggleSelect}
                onStop={onStop}
                onDelete={onDelete}
                fmtPnl={fmtPnl}
                fmtVol={fmtVol}
                fmtDet={fmtDet}
              />
            ))}
          </tbody>
        </table>
      </div>
      {remaining > 0 && (
        <div className="flex items-center justify-center gap-3 border-t border-[var(--color-border)] px-4 py-2.5">
          <span className="text-xs text-[var(--color-text-muted)] tabular-nums">
            Showing {visible.length} of {sorted.length}
          </span>
          <button
            onClick={() => setVisibleCount((c) => c + VISIBLE_STEP)}
            className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium hover:bg-[var(--color-surface-hover)] transition-colors"
          >
            Show {Math.min(VISIBLE_STEP, remaining)} more
          </button>
        </div>
      )}
    </div>
  );
}

// ── Detail Panel ──

/** `YYYY-MM-DD HH:mm` in local time, the charts' crosshair format; fits half a card. */
function formatStamp(seconds: number): string {
  const d = new Date(seconds * 1000);
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/**
 * Everything known about one executor beyond its chart: prices, PnL, the
 * type's own settings, timing, custom info and the raw config. The side panel
 * stacks it under its chart; a single-executor scope draws it inline in two
 * columns, where the page above already is that executor's chart.
 */
export function ExecutorDetails({
  executor,
  layout = "stack",
  rateFormatPnl,
  rateFormatValue,
  rateFormatDetailed,
}: {
  executor: ExecutorInfo;
  layout?: "stack" | "columns";
  rateFormatPnl?: (val: number, quote: string) => string;
  rateFormatValue?: (val: number, quote: string) => string;
  rateFormatDetailed?: (val: number, quote: string) => string;
}) {
  const configEntries = Object.entries(executor.config || {});
  const customEntries = Object.entries(executor.custom_info || {});

  const config = executor.config || {};
  const isPosition = executor.type === "position";
  const isGrid = executor.type === "grid";
  const isLp = executor.type === "lp";
  // A CLMM position is snapped to the venue's bins, so the on-chain bounds differ
  // from the requested ones. Show where the liquidity actually sits.
  const custom = executor.custom_info || {};
  const lpLower = custom.lower_price ?? custom.price_lower ?? config.lower_price;
  const lpUpper = custom.upper_price ?? custom.price_upper ?? config.upper_price;

  // Parse triple_barrier_config (may be a JSON string or object)
  const tripleBarrier: Record<string, unknown> = (() => {
    const raw = config.triple_barrier_config;
    if (!raw) return {};
    if (typeof raw === "string") {
      try { return JSON.parse(raw); } catch { return {}; }
    }
    return typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  })();

  const quote = executor.trading_pair?.split("-")[1] || "USDT";
  const fmtPnl = rateFormatPnl ? (v: number) => rateFormatPnl(v, quote) : formatPnl;
  const fmtVal = rateFormatValue ? (v: number) => rateFormatValue(v, quote) : formatUsd;
  const fmtDet = rateFormatDetailed ? (v: number) => rateFormatDetailed(v, quote) : formatUsd;

  const columns = layout === "columns";
  const priceCard = (
    <>
        {/* Price Info */}
        {(executor.entry_price > 0 || executor.current_price > 0) && (
          <Card className="space-y-3">
            <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Price Info
            </h3>
            <div className="grid grid-cols-2 gap-3 text-sm">
              {executor.entry_price > 0 && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Entry</div>
                  <div className="font-medium tabular-nums">{formatPrice(executor.entry_price)}</div>
                </div>
              )}
              {executor.current_price > 0 && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Current</div>
                  <div className="font-medium tabular-nums">{formatPrice(executor.current_price)}</div>
                </div>
              )}
            </div>
          </Card>
        )}
    </>
  );
  const pnlCard = (
    <>
        {/* PnL Breakdown */}
        <Card className="space-y-3">
          <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            PnL & Volume
          </h3>
          <div className={`grid gap-3 text-sm ${columns ? "grid-cols-2" : "grid-cols-4"}`}>
            <div>
              <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Net PnL</div>
              <div className="font-medium tabular-nums" style={{ color: pnlColor(executor.pnl) }}>
                {fmtPnl(executor.pnl)}
              </div>
            </div>
            <div>
              <div className="text-[var(--color-text-muted)] text-xs mb-0.5">PnL %</div>
              <div
                className="font-medium tabular-nums"
                style={{ color: executor.net_pnl_pct ? pnlColor(executor.net_pnl_pct) : undefined }}
              >
                {formatPct(executor.net_pnl_pct)}
              </div>
            </div>
            <div>
              <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Volume</div>
              <div className="font-medium tabular-nums">{fmtVal(executor.volume)}</div>
            </div>
            <div>
              <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Fees</div>
              <div className="font-medium tabular-nums">
                {executor.cum_fees_quote ? fmtDet(executor.cum_fees_quote) : "\u2014"}
              </div>
            </div>
          </div>
        </Card>
    </>
  );
  const typeCard = (
    <>
        {/* Position-specific details */}
        {isPosition && (
          <Card className="space-y-3">
            <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Position Details
            </h3>
            <div className="grid grid-cols-2 gap-3 text-sm">
              {config.stop_loss != null && Number(config.stop_loss) !== -1 && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Stop Loss</div>
                  <div className="font-medium tabular-nums text-[var(--color-red)]">
                    {(Number(config.stop_loss) * 100).toFixed(2)}%
                  </div>
                </div>
              )}
              {config.take_profit != null && Number(config.take_profit) !== -1 && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Take Profit</div>
                  <div className="font-medium tabular-nums text-[var(--color-green)]">
                    {(Number(config.take_profit) * 100).toFixed(2)}%
                  </div>
                </div>
              )}
              {config.leverage != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Leverage</div>
                  <div className="font-medium tabular-nums">{String(config.leverage)}x</div>
                </div>
              )}
              {config.total_amount_quote != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Amount</div>
                  <div className="font-medium tabular-nums">{fmtDet(Number(config.total_amount_quote))}</div>
                </div>
              )}
            </div>
          </Card>
        )}

        {/* Grid-specific details */}
        {isGrid && (
          <Card className="space-y-3">
            <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Grid Details
            </h3>
            <div className="grid grid-cols-2 gap-3 text-sm">
              {config.start_price != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Start Price</div>
                  <div className="font-medium tabular-nums">{formatPrice(Number(config.start_price))}</div>
                </div>
              )}
              {config.end_price != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">End Price</div>
                  <div className="font-medium tabular-nums">{formatPrice(Number(config.end_price))}</div>
                </div>
              )}
              {config.limit_price != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Limit Price</div>
                  <div className="font-medium tabular-nums">{formatPrice(Number(config.limit_price))}</div>
                </div>
              )}
              {config.leverage != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Leverage</div>
                  <div className="font-medium tabular-nums">{String(config.leverage)}x</div>
                </div>
              )}
              {config.total_amount_quote != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Amount</div>
                  <div className="font-medium tabular-nums">{fmtDet(Number(config.total_amount_quote))}</div>
                </div>
              )}
              {tripleBarrier.take_profit != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Take Profit</div>
                  <div className="font-medium tabular-nums text-[var(--color-green)]">
                    {(Number(tripleBarrier.take_profit) * 100).toFixed(2)}%
                  </div>
                </div>
              )}
              {config.keep_position != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Keep Position</div>
                  <div className="font-medium">{String(config.keep_position) === "true" ? "Yes" : "No"}</div>
                </div>
              )}
              {tripleBarrier.open_order_type != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Open Order Type</div>
                  <div className="font-medium text-xs">{String(tripleBarrier.open_order_type)}</div>
                </div>
              )}
              {tripleBarrier.take_profit_order_type != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">TP Order Type</div>
                  <div className="font-medium text-xs">{String(tripleBarrier.take_profit_order_type)}</div>
                </div>
              )}
            </div>
          </Card>
        )}

        {/* LP-specific details */}
        {isLp && (
          <Card className="space-y-3">
            <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              LP Details
            </h3>
            <div className="grid grid-cols-2 gap-3 text-sm">
              {lpLower != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Lower Price</div>
                  <div className="font-medium tabular-nums">{formatPrice(Number(lpLower))}</div>
                </div>
              )}
              {lpUpper != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Upper Price</div>
                  <div className="font-medium tabular-nums">{formatPrice(Number(lpUpper))}</div>
                </div>
              )}
              {config.lower_limit_price != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Lower Limit</div>
                  <div className="font-medium tabular-nums text-[var(--color-red)]">
                    {formatPrice(Number(config.lower_limit_price))}
                  </div>
                </div>
              )}
              {config.upper_limit_price != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Upper Limit</div>
                  <div className="font-medium tabular-nums text-[var(--color-red)]">
                    {formatPrice(Number(config.upper_limit_price))}
                  </div>
                </div>
              )}
              {config.lp_provider != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Provider</div>
                  <div className="font-medium text-xs">{String(config.lp_provider)}</div>
                </div>
              )}
              {custom.in_range != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Range Status</div>
                  <div className="font-medium text-xs">{String(custom.in_range)}</div>
                </div>
              )}
              {config.base_amount != null && Number(config.base_amount) > 0 && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Base Amount</div>
                  <div className="font-medium tabular-nums">{String(config.base_amount)}</div>
                </div>
              )}
              {config.quote_amount != null && Number(config.quote_amount) > 0 && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Quote Amount</div>
                  <div className="font-medium tabular-nums">{String(config.quote_amount)}</div>
                </div>
              )}
              {config.pool_address != null && (
                <div className="col-span-2">
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Pool</div>
                  <div className="font-mono text-[11px] break-all">{String(config.pool_address)}</div>
                </div>
              )}
              {config.keep_position != null && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Keep Position</div>
                  <div className="font-medium">{String(config.keep_position) === "true" ? "Yes" : "No"}</div>
                </div>
              )}
            </div>
          </Card>
        )}
    </>
  );
  const timingCard = (
    <>
        {/* Timestamps */}
        {executor.timestamp > 0 && (
          <Card className="space-y-3">
            <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Timing
            </h3>
            <div className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Created</div>
                <div className="font-medium tabular-nums" title={new Date(executor.timestamp * 1000).toLocaleString()}>
                {formatStamp(executor.timestamp)}
              </div>
                <div className="text-xs text-[var(--color-text-muted)]">{formatAge(executor.timestamp)} ago</div>
              </div>
              {executor.close_timestamp > 0 && (
                <div>
                  <div className="text-[var(--color-text-muted)] text-xs mb-0.5">Closed</div>
                  <div className="font-medium tabular-nums" title={new Date(executor.close_timestamp * 1000).toLocaleString()}>
                  {formatStamp(executor.close_timestamp)}
                </div>
                </div>
              )}
            </div>
          </Card>
        )}
    </>
  );
  const customCard = (
    <>
        {/* Custom Info */}
        {customEntries.length > 0 && (
          <Card className="space-y-2">
            <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Custom Info
            </h3>
            <div className="space-y-1 text-xs">
              {customEntries.map(([key, val]) => (
                <div key={key} className="flex justify-between gap-3 py-0.5">
                  <span className="text-[var(--color-text-muted)] shrink-0">{key}</span>
                  <span className="tabular-nums text-right truncate" title={String(val ?? "")}>
                    {typeof val === "object" && val !== null
                      ? JSON.stringify(val)
                      : String(val ?? "")}
                  </span>
                </div>
              ))}
            </div>
          </Card>
        )}
    </>
  );
  const configRows = (
    <div className="space-y-1 text-xs">
      {configEntries.map(([key, val]) => {
              // Parse JSON strings into objects for display
              let parsed = val;
              if (typeof val === "string") {
                try { const p = JSON.parse(val); if (typeof p === "object" && p !== null) parsed = p; } catch {}
              }
              const isNested = typeof parsed === "object" && parsed !== null;

              if (isNested) {
                return (
                  <details key={key} className="py-0.5">
                    <summary className="flex justify-between gap-3 cursor-pointer hover:text-[var(--color-text)]">
                      <span className="text-[var(--color-text-muted)] shrink-0">{key}</span>
                      <span className="text-[var(--color-text-muted)]">{Object.keys(parsed as object).length} fields</span>
                    </summary>
                    <div className="ml-3 pl-3 border-l border-[var(--color-border)]/50 mt-1 space-y-0.5">
                      {Object.entries(parsed as Record<string, unknown>).map(([k, v]) => (
                        <div key={k} className="flex justify-between gap-3 py-0.5">
                          <span className="text-[var(--color-text-muted)] shrink-0">{k}</span>
                          <span className="tabular-nums text-right truncate" title={String(v ?? "")}>
                            {v == null ? "null" : String(v)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </details>
                );
              }

              return (
                <div key={key} className="flex justify-between gap-3 py-0.5">
                  <span className="text-[var(--color-text-muted)] shrink-0">{key}</span>
                  <span className="tabular-nums text-right truncate" title={String(val ?? "")}>
                    {String(val ?? "")}
                  </span>
                </div>
              );
            })}
    </div>
  );
  const configTitle = `Raw Config (${configEntries.length} fields)`;

  if (columns) {
    // Two rows of equal-height cards: the label-over-value summaries, then the
    // two key-value lists side by side (the config open, as a card).
    return (
      <div className="space-y-3">
        {/* As many equal columns as fit: three or four summaries share one row
            instead of leaving one alone on a second. */}
        <div className="grid grid-cols-[repeat(auto-fit,minmax(13rem,1fr))] gap-3">
          {priceCard}
          {pnlCard}
          {timingCard}
          {typeCard}
        </div>
        {(customEntries.length > 0 || configEntries.length > 0) && (
          <div className="grid gap-3 md:grid-cols-2 md:[&>:only-child]:col-span-2">
            {customCard}
            {configEntries.length > 0 && (
              <Card className="space-y-2">
                <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  {configTitle}
                </h3>
                {configRows}
              </Card>
            )}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-5">
      {priceCard}
      {pnlCard}
      {typeCard}
      {timingCard}
      {customCard}
      {configEntries.length > 0 && (
        <details className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
          <summary className="px-4 py-3 text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)] cursor-pointer select-none hover:text-[var(--color-text)]">
            {configTitle}
          </summary>
          <div className="px-4 pb-3">{configRows}</div>
        </details>
      )}
    </div>
  );
}

export function DetailPanel({
  executor,
  server,
  onClose,
  onStop,
  stopping,
  onDelete,
  deleting,
  rateFormatPnl,
  rateFormatValue,
  rateFormatDetailed,
}: {
  executor: ExecutorInfo;
  server: string;
  onClose: () => void;
  /** Omit for a read-only panel: no Stop button is drawn without it. */
  onStop?: (id: string) => void;
  stopping?: boolean;
  onDelete?: (id: string) => void;
  deleting?: boolean;
  rateFormatPnl?: (val: number, quote: string) => string;
  rateFormatValue?: (val: number, quote: string) => string;
  rateFormatDetailed?: (val: number, quote: string) => string;
}) {
  const navigate = useNavigate();
  const [panelWidth, setPanelWidth] = useState(480);

  const { onMouseDown } = useResizeDrag({
    axis: "x",
    value: panelWidth,
    onChange: setPanelWidth,
    min: 300,
    max: () => window.innerWidth * 0.8,
    compute: (coord) => window.innerWidth - coord,
    cursor: "col-resize",
    lockUserSelect: true,
  });

  // One executor, but the chart takes a list. Minting the array inline handed
  // the chart a new prop on every render — and dragging the divider above renders
  // on every `mousemove` — so the group it charts is held still here instead.
  const chartExecutors = useMemo(() => [executor], [executor]);

  const sideLabel = executor.side.toUpperCase();
  // The backend normalizes every side to BUY/SELL (normalize_executor_side), so
  // no raw "1"/"TradeType.BUY" fallback is needed here.
  const sideColor = sideLabel === "BUY" ? "var(--color-green)" : "var(--color-red)";
  const sideBg = sideLabel === "BUY" ? "rgba(34,197,94,0.1)" : "rgba(239,68,68,0.1)";

  return (
      <div
        className="h-full bg-[var(--color-bg)] border-l border-[var(--color-border)] overflow-y-auto shadow-xl shrink-0 relative"
        style={{ width: panelWidth }}
      >
        <div
          className="absolute left-0 top-0 z-30 h-full w-1.5 cursor-col-resize transition-colors hover:bg-[var(--color-primary)]/30"
          onMouseDown={onMouseDown}
        />

        {/* Stays above the body while it scrolls, which keeps the chart's
            layers in its own stacking context; only the title gives way when
            the panel is narrow. */}
        <div className="sticky top-0 z-20 flex h-14 items-center gap-3 border-b border-[var(--color-border)] bg-[var(--color-bg)] px-5">
          <div className="min-w-0 flex-1">
            <h2 className="truncate font-mono text-sm font-semibold" title={executor.id}>
              {executor.id}
            </h2>
            <p className="truncate text-xs text-[var(--color-text-muted)]">
              {executor.trading_pair} · {formatConnectorName(executor.connector)}
            </p>
          </div>
          <div className="flex shrink-0 items-center gap-1.5">
            <button
              onClick={() => navigate(`/trade?connector=${encodeURIComponent(executor.connector)}&pair=${encodeURIComponent(executor.trading_pair)}`)}
              className="flex h-8 items-center gap-1.5 whitespace-nowrap rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 text-xs font-medium transition-colors hover:bg-[var(--color-surface-hover)]"
            >
              <TrendingUp className="h-3 w-3" />
              Trade
            </button>
            {onStop && isExecutorActive(executor.status) && (
              <button
                onClick={() => onStop(executor.id)}
                disabled={stopping}
                className="flex h-8 items-center gap-1.5 whitespace-nowrap rounded-md bg-[var(--color-red)] px-2.5 text-xs font-medium text-white transition-colors hover:opacity-90 disabled:opacity-50"
              >
                <Square className="h-3 w-3" />
                {stopping ? "Stopping\u2026" : "Stop"}
              </button>
            )}
            {onDelete && isExecutorTerminated(executor.status) && (
              <button
                onClick={() => onDelete(executor.id)}
                disabled={deleting}
                title="Delete this executor's history"
                className="flex h-8 items-center gap-1.5 whitespace-nowrap rounded-md bg-[var(--color-red)] px-2.5 text-xs font-medium text-white transition-colors hover:opacity-90 disabled:opacity-50"
              >
                <Trash2 className="h-3 w-3" />
                {deleting ? "Deleting…" : "Delete"}
              </button>
            )}
            <button
              onClick={onClose}
              className="flex h-8 w-8 items-center justify-center rounded-md transition-colors hover:bg-[var(--color-surface-hover)]"
              title="Close"
              aria-label="Close"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        <div className="relative z-0 space-y-5 p-5">
          {/* Status & Meta */}
          <div className="flex items-center gap-3 flex-wrap text-sm">
            <div className="flex items-center gap-1.5">
              <StatusDot status={executor.status} />
              <span className="capitalize">{executor.status}</span>
            </div>
            <span className="rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs font-medium border border-[var(--color-border)]/50">
              {executor.type}
            </span>
            <span className="text-[var(--color-text-muted)]">{executor.connector}</span>
            <PairLabel tradingPair={executor.trading_pair} connector={executor.connector} />
            <span
              className="rounded px-1.5 py-0.5 text-xs font-semibold uppercase"
              style={{ color: sideColor, background: sideBg }}
            >
              {sideLabel}
            </span>
            {executor.close_type && (
              <span className="rounded bg-[var(--color-surface)] px-2 py-0.5 text-xs font-medium border border-[var(--color-border)]/50">
                {executor.close_type}
              </span>
            )}
          </div>

          {/* Controller ID */}
          {executor.controller_id && (
            <Card className="space-y-1">
              <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                Controller
              </h3>
              <div className="text-sm font-medium font-mono">{executor.controller_id}</div>
            </Card>
          )}

          {/* Executor Chart */}
          {server && executor.connector && executor.trading_pair && (
            <ExecutorChart
              server={server}
              executors={chartExecutors}
              connector={executor.connector}
              tradingPair={executor.trading_pair}
              height={380}
            />
          )}

          <ExecutorDetails
            executor={executor}
            rateFormatPnl={rateFormatPnl}
            rateFormatValue={rateFormatValue}
            rateFormatDetailed={rateFormatDetailed}
          />
        </div>
      </div>
  );
}
