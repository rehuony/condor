import type { ReactNode } from "react";

import { ConfirmDialog as SharedConfirmDialog } from "@/components/ui/ConfirmDialog";

/**
 * Shared destructive-confirmation modal for agent/strategy delete flows.
 *
 * Renders the overlay + card + Cancel + destructive button shell that was
 * previously copy-pasted across Agents.tsx and AgentDetail.tsx (ARCH-059).
 * The destructive button uses the themed `bg-[var(--color-red)]` token so it
 * honors the colorblind theme's red remap (index.css), matching the editor
 * dialogs (EditorDialogs.tsx) instead of a hardcoded `bg-red-500`.
 *
 * Closes on Escape via `useEscapeKey` (CORR-077) while `open` is true.
 *
 * Drawn by `components/ui/ConfirmDialog`, so every confirmation in the app
 * shares one look; this keeps the `open`/`isPending` API its callers use.
 */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel = "Delete",
  pendingLabel = "Deleting...",
  isPending = false,
  isError = false,
  errorText,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel?: string;
  pendingLabel?: string;
  isPending?: boolean;
  isError?: boolean;
  errorText?: string;
  onConfirm: () => void;
  onClose: () => void;
}) {
  if (!open) return null;
  // The app's one confirmation look (components/ui/ConfirmDialog).
  return (
    <SharedConfirmDialog
      title={title}
      confirmLabel={confirmLabel}
      pendingLabel={pendingLabel}
      pending={isPending}
      error={isError && errorText ? errorText : null}
      onConfirm={onConfirm}
      onCancel={onClose}
    >
      {children}
    </SharedConfirmDialog>
  );
}
