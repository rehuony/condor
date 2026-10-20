/**
 * Class strings for data tables, so a new table reads like the others.
 *
 * Strings rather than components because every table here owns its `<table>`
 * markup (sticky identity columns, expandable rows, container queries) and
 * only wants the shared look of the header row and its cells.
 *
 * Two densities: the default (`TH`/`TD`, `py-2`) for detail pages, and the
 * `DENSE_*` set (`py-1.5`) for tables embedded in a pane or a report.
 */

/** Header row: a bottom rule and muted labels; the cells carry alignment. */
export const TABLE_HEAD_ROW = "border-b border-[var(--color-border)] text-[var(--color-text-muted)]";

/** Header cells: left-aligned text, right-aligned numbers. */
export const TH = "px-3 py-2 text-left font-medium";
export const TH_NUM = "px-3 py-2 text-right font-medium";

/** Body cells matching `TH`/`TH_NUM`; numbers also take `font-mono` or `tabular-nums`. */
export const TD = "px-3 py-2";
export const TD_NUM = "px-3 py-2 text-right font-mono";

export const DENSE_TH = "px-3 py-1.5 text-left font-medium";
export const DENSE_TH_NUM = "px-3 py-1.5 text-right font-medium";
export const DENSE_TD = "px-3 py-1.5";
export const DENSE_TD_NUM = "px-3 py-1.5 text-right font-mono";
