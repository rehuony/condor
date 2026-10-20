import { useCallback, useState } from "react";

// One text line tall with its label centered in it. The pseudo-element enlarges
// the touch target without moving the label or the text around it.
const TOGGLE =
  "flex h-[1.625em] items-center text-sm font-medium text-[var(--color-primary)] hover:underline focus-visible:outline-2 focus-visible:outline-[var(--color-primary)] before:absolute before:-inset-y-2.5 before:-inset-x-2 before:content-['']";

/**
 * The ask above a task's transcript or result, collapsed to two lines.
 *
 * The answer is what the sheet is opened for, so the ask yields its height by
 * default. The text keeps the full width: "Show more" sits over the end of the
 * clamped last line, and "Show less" follows the expanded text, which is still
 * bounded so the result keeps most of a phone screen.
 */
export function DelegationTaskPrompt({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const [clamped, setClamped] = useState(false);

  const measure = useCallback((node: HTMLParagraphElement | null) => {
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      // Only the collapsed state can tell whether the text overflows two lines.
      if (node.dataset.expanded !== "true") setClamped(node.scrollHeight > node.clientHeight + 1);
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="shrink-0 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2.5">
      <div className="relative">
        <p
          ref={measure}
          data-expanded={expanded}
          className={`whitespace-pre-wrap break-words text-sm leading-relaxed text-[var(--color-text)] ${
            expanded ? "max-h-[35dvh] overflow-y-auto" : "line-clamp-2"
          }`}
        >
          {text}
        </p>
        {!expanded && clamped && (
          // Covers the line's own ellipsis with one that stays visible.
          <button
            type="button"
            aria-expanded={false}
            onClick={() => setExpanded(true)}
            className={`${TOGGLE} absolute bottom-0 right-0 bg-[var(--color-surface)] pl-1`}
          >
            <span aria-hidden className="font-normal text-[var(--color-text)]">…&nbsp;</span>
            Show more
          </button>
        )}
      </div>
      {expanded && (
        <div className="mt-1 flex justify-end">
          <button type="button" aria-expanded onClick={() => setExpanded(false)} className={`${TOGGLE} relative`}>
            Show less
          </button>
        </div>
      )}
    </div>
  );
}
