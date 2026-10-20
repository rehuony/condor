import { formatUsd } from "@/lib/formatters";


/** Fees on a fresh position are fractions of a cent, where `$0.00` says nothing. */
export function feeAmount(val: number): string {
  if (val !== 0 && Math.abs(val) < 0.01) return `$${val.toPrecision(2)}`;
  return formatUsd(val);
}


/** In range is earning; out of range is not, and is the thing worth spotting. */
export function lpStateStyle(state: string): {
  label: string;
  color: string;
  bg: string;
} {
  const upper = state.toUpperCase();
  if (upper === "IN_RANGE") {
    return {
      label: "In range",
      color: "var(--color-green)",
      bg: "rgba(34,197,94,0.12)",
    };
  }
  if (upper === "OUT_OF_RANGE") {
    return {
      label: "Out of range",
      color: "var(--color-yellow)",
      bg: "rgba(234,179,8,0.14)",
    };
  }
  return {
    label: upper ? upper.replace(/_/g, " ").toLowerCase() : "open",
    color: "var(--color-text-muted)",
    bg: "var(--color-surface)",
  };
}
