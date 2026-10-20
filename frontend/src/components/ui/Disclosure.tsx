import { ChevronRight } from "lucide-react";
import { type ReactNode, useId, useState } from "react";

/**
 * A section that opens and closes under a heading: a button carrying
 * `aria-expanded` and a chevron that turns to point down, then the body.
 *
 * Works uncontrolled (`defaultOpen`) or controlled (`open` + `onOpenChange`),
 * because some callers persist the state and some only hold it for the
 * visit. The body is unmounted while closed, so a query gated on `open`
 * does not run for a section nobody looked at.
 */
export interface DisclosureProps {
  /** What the button shows; the chevron is added before or after it. */
  summary: ReactNode;
  children: ReactNode;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Wrapper element classes, e.g. the card border. */
  className?: string;
  /** Replaces the button's default padding and layout; alignment and width stay. */
  buttonClassName?: string;
  /** Classes on the open body, e.g. a top border and padding. */
  bodyClassName?: string;
  chevron?: "start" | "end";
  chevronClassName?: string;
  /** Rendered right after the summary, outside the button's text (e.g. a count). */
  trailing?: ReactNode;
  disabled?: boolean;
}

export function Disclosure({
  summary,
  children,
  open: controlled,
  defaultOpen = false,
  onOpenChange,
  className,
  buttonClassName = "gap-2 px-4 py-3",
  bodyClassName,
  chevron = "start",
  chevronClassName = "h-3.5 w-3.5 text-[var(--color-text-muted)]",
  trailing,
  disabled = false,
}: DisclosureProps) {
  const [uncontrolled, setUncontrolled] = useState(defaultOpen);
  const open = controlled ?? uncontrolled;
  const bodyId = useId();

  const toggle = () => {
    const next = !open;
    if (controlled === undefined) setUncontrolled(next);
    onOpenChange?.(next);
  };

  const icon = (
    <ChevronRight
      aria-hidden
      className={`shrink-0 transition-transform motion-reduce:transition-none ${open ? "rotate-90" : ""} ${chevronClassName}`}
    />
  );

  return (
    <div className={className}>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? bodyId : undefined}
        onClick={toggle}
        disabled={disabled}
        className={`flex w-full items-center text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${buttonClassName}`}
      >
        {chevron === "start" && icon}
        {summary}
        {trailing}
        {chevron === "end" && icon}
      </button>
      {open && (
        <div id={bodyId} className={bodyClassName}>
          {children}
        </div>
      )}
    </div>
  );
}
