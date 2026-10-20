import type { LabelHTMLAttributes, ReactNode } from "react";

/**
 * Form field chrome: the small muted label above a control, the control's
 * own classes, and `Field` putting the two together with an optional hint.
 *
 * `INPUT_CLASS` is the text input; append `font-mono` for keys and addresses.
 */
export const LABEL_CLASS = "mb-1 block text-xs text-[var(--color-text-muted)]";

export const INPUT_CLASS =
  "w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none disabled:cursor-not-allowed disabled:opacity-50";

export function Label({ className, ...rest }: LabelHTMLAttributes<HTMLLabelElement>) {
  return <label className={className ? `${LABEL_CLASS} ${className}` : LABEL_CLASS} {...rest} />;
}

export interface FieldProps {
  label: ReactNode;
  /** The control's `id`, so clicking the label focuses it. */
  htmlFor?: string;
  children: ReactNode;
  /** One line under the control: a format hint or the validation message. */
  hint?: ReactNode;
  className?: string;
}

export function Field({ label, htmlFor, children, hint, className }: FieldProps) {
  return (
    <div className={className}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint && <p className="mt-1 text-xs text-[var(--color-text-muted)]">{hint}</p>}
    </div>
  );
}
