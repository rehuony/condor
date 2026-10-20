import type { ButtonHTMLAttributes } from "react";

/**
 * A button that is only an icon: close, back, copy, refresh, collapse.
 *
 * The icon has no text, so `label` is required and becomes the accessible
 * name. `tone` says what the hover color promises: `default` brightens to
 * body text, `primary` to gold, `danger` to red with a red tint.
 */
const SIZES = {
  xs: "p-0.5",
  sm: "p-1",
  md: "p-1.5",
  lg: "p-2",
} as const;

const TONES = {
  default: "hover:enabled:bg-[var(--color-surface-hover)] hover:enabled:text-[var(--color-text)]",
  primary: "hover:enabled:bg-[var(--color-surface-hover)] hover:enabled:text-[var(--color-primary)]",
  danger: "hover:enabled:bg-[var(--color-red)]/10 hover:enabled:text-[var(--color-red)]",
} as const;

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "aria-label"> {
  /** Accessible name; also the tooltip unless `title` is given. */
  label: string;
  size?: keyof typeof SIZES;
  tone?: keyof typeof TONES;
}

export function IconButton({
  label,
  size = "md",
  tone = "default",
  className,
  type = "button",
  title,
  ...rest
}: IconButtonProps) {
  const base = `shrink-0 rounded text-[var(--color-text-muted)] transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--color-primary)] disabled:cursor-not-allowed disabled:opacity-50 ${SIZES[size]} ${TONES[tone]}`;
  return (
    <button
      type={type}
      aria-label={label}
      title={title ?? label}
      className={className ? `${base} ${className}` : base}
      {...rest}
    />
  );
}
