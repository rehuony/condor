import { useId, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { useEscapeKey } from "@/hooks/useEscapeKey";

import {
  DIALOG_BACKDROP,
  DIALOG_CANCEL,
  DIALOG_DANGER,
  DIALOG_FOOTER,
  DIALOG_PANEL,
  DIALOG_PRIMARY,
  handleDialogKeyDown,
} from "./dialog";

interface ConfirmDialogProps {
  title: string;
  children?: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  /** Shown on the confirm button while the action runs. */
  pendingLabel?: string;
  pending?: boolean;
  /** Keeps the confirm button disabled, e.g. until a required choice is made. */
  confirmDisabled?: boolean;
  error?: string | null;
  /** `danger` for removals; `primary` for an action that only starts work. */
  tone?: "danger" | "primary";
  /** For callers and tests that address the title by a fixed id. */
  titleId?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * A modal that asks before a destructive or far-reaching action: a title that
 * is the question, at most one line saying what it touches, and two buttons.
 * Cancel takes focus, and Escape or the backdrop dismiss it unless the action
 * is running.
 *
 * Portalled to `<body>`: a caller inside a collapsed rail, a clipped pane or a
 * layered sheet must not be able to hide or cover it.
 */
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  cancelLabel = "Cancel",
  pendingLabel = "Deleting…",
  pending = false,
  confirmDisabled = false,
  error,
  tone = "danger",
  titleId,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const generatedId = useId();
  const headingId = titleId ?? generatedId;
  useEscapeKey(!pending, onCancel);
  const danger = tone === "danger";
  return createPortal(
    <div className={`${DIALOG_BACKDROP} z-[60]`} onClick={pending ? undefined : onCancel}>
      <div
        ref={(node) => { if (pending) node?.focus({ preventScroll: true }); }}
        role="dialog"
        tabIndex={-1}
        aria-modal="true"
        aria-labelledby={headingId}
        className={`${DIALOG_PANEL} max-h-[85dvh] max-w-sm bg-[var(--color-surface)] p-5`}
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(event) => handleDialogKeyDown(event, pending, onCancel)}
      >
        <h3 id={headingId} className="text-base font-semibold break-words text-[var(--color-text)]">{title}</h3>
        {children && (
          <div className="mt-2 space-y-2 text-sm leading-relaxed break-words text-[var(--color-text-muted)]">{children}</div>
        )}
        {error && (
          <p role="alert" className="mt-3 rounded-lg bg-[var(--color-red)]/10 px-3 py-2 text-xs break-words text-[var(--color-red)]">
            {error}
          </p>
        )}
        <div className={`${DIALOG_FOOTER} mt-5`}>
          <button type="button" onClick={onCancel} disabled={pending} autoFocus className={DIALOG_CANCEL}>
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            disabled={pending || confirmDisabled}
            className={danger ? DIALOG_DANGER : DIALOG_PRIMARY}
          >
            {pending ? pendingLabel : confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
