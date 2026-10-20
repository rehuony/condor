import type { Grain } from "@/lib/perf-tree";

/**
 * How far back a terminated fold reaches, and how long that is in ms.
 *
 * Only the terminated population offers this: a live controller's window is
 * its deploy time, which it already reports, and cutting a live fold at a week
 * would report a PnL its own Realized tile disagrees with.
 */
export const PERIODS = { "1W": 7, "1M": 30, "3M": 90, All: 0 } as const;
export type PeriodKey = keyof typeof PERIODS;

/**
 * The granularities the tree can be drawn at, and what each strip segment says.
 *
 * A strip rather than a bubble row: the bubble groups are multi-select, and
 * these three are one answer — the same shape the period strip below it has,
 * for the same reason.
 */
export const GRAINS: ReadonlyArray<{ key: Grain; label: string; hint: string }> = [
  { key: "both", label: "Both", hint: "Every record: controllers, with the executors under them." },
  {
    key: "controllers",
    label: "Controllers",
    hint: "Controller records only — each row reports its own numbers, with no executors under it.",
  },
  {
    key: "executors",
    label: "Executors",
    hint: "Executors only — a controller row folds the executors that ran under it, and reports nothing of its own.",
  },
];
