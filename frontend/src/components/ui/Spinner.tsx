import { Loader2 } from "lucide-react";

/**
 * The one loading indicator. It takes `currentColor`, so inside a button it
 * is the button's text color; standing alone, ask for the muted tone.
 *
 * `xs` sits inside pills and dense controls, `sm`/`md` in buttons and table
 * cells, `lg` in panels, `xl` for a whole screen that is still loading.
 */
const SIZES = {
  xs: "h-3 w-3",
  sm: "h-3.5 w-3.5",
  md: "h-4 w-4",
  lg: "h-5 w-5",
  xl: "h-6 w-6",
} as const;

const TONES = {
  inherit: "",
  muted: "text-[var(--color-text-muted)]",
  primary: "text-[var(--color-primary)]",
} as const;

export type SpinnerSize = keyof typeof SIZES;

export interface SpinnerProps {
  size?: SpinnerSize;
  tone?: keyof typeof TONES;
  /** Layout classes (margins, `inline`); keep size, color and `animate-spin` out of it. */
  className?: string;
  /** Accessible status text; omit when the surrounding text already says "Loading". */
  label?: string;
}

export function Spinner({ size = "md", tone = "inherit", className, label }: SpinnerProps) {
  const classes = [SIZES[size], "shrink-0 animate-spin", TONES[tone], className].filter(Boolean).join(" ");
  return (
    <Loader2
      data-spinner=""
      role={label ? "status" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={classes}
    />
  );
}

/** A muted spinner centred in its own block, for a pane whose content is still loading. */
export function SpinnerBlock({ size = "lg", className = "py-8", label }: SpinnerProps) {
  return (
    <div className={`flex items-center justify-center ${className}`}>
      <Spinner size={size} tone="muted" label={label} />
    </div>
  );
}
