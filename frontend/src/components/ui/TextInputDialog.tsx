import { useId, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";

import { useEscapeKey } from "@/hooks/useEscapeKey";

import {
  DIALOG_BACKDROP,
  DIALOG_CANCEL,
  DIALOG_FOOTER,
  DIALOG_PANEL,
  DIALOG_PRIMARY,
  handleDialogKeyDown,
} from "./dialog";

/**
 * A modal that asks for one line of text, such as a new name. Same frame as
 * `ConfirmDialog`; Enter submits, Escape or the backdrop cancel, and an empty
 * value cannot be submitted. Portalled to `<body>` like `ConfirmDialog`.
 */
export function TextInputDialog({
  title,
  label,
  initialValue = "",
  placeholder,
  confirmLabel = "Save",
  pending = false,
  error,
  onSubmit,
  onCancel,
}: {
  title: string;
  label: string;
  initialValue?: string;
  placeholder?: string;
  confirmLabel?: string;
  pending?: boolean;
  error?: string | null;
  onSubmit: (value: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initialValue);
  const titleId = useId();
  const inputId = useId();
  useEscapeKey(!pending, onCancel);
  const trimmed = value.trim();

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (trimmed && !pending) onSubmit(trimmed);
  };

  return createPortal(
    <div className={`${DIALOG_BACKDROP} z-[60]`} onClick={pending ? undefined : onCancel}>
      <form
        ref={(node) => { if (pending) node?.focus({ preventScroll: true }); }}
        role="dialog"
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby={titleId}
        onSubmit={submit}
        className={`${DIALOG_PANEL} max-h-[85dvh] max-w-sm bg-[var(--color-surface)] p-5`}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(event) => handleDialogKeyDown(event, pending, onCancel)}
      >
        <h3 id={titleId} className="text-base font-semibold text-[var(--color-text)]">{title}</h3>
        <label htmlFor={inputId} className="sr-only">
          {label}
        </label>
        <input
          id={inputId}
          autoFocus
          onFocus={(e) => e.currentTarget.select()}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          disabled={pending}
          className="mt-3 h-9 w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-bg)] px-3 text-sm text-[var(--color-text)] transition-colors placeholder:text-[var(--color-text-muted)] focus:border-[var(--color-primary)] focus:outline-none disabled:opacity-60"
        />
        {error && (
          <p role="alert" className="mt-2 rounded-lg bg-[var(--color-red)]/10 px-3 py-2 text-xs break-words text-[var(--color-red)]">
            {error}
          </p>
        )}
        <div className={`${DIALOG_FOOTER} mt-5`}>
          <button type="button" onClick={onCancel} disabled={pending} className={DIALOG_CANCEL}>
            Cancel
          </button>
          <button type="submit" disabled={!trimmed || pending} className={DIALOG_PRIMARY}>
            {pending ? "Saving…" : confirmLabel}
          </button>
        </div>
      </form>
    </div>,
    document.body,
  );
}
