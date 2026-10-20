import { ChevronDown, Check } from "lucide-react";
import { useCallback, useId, useRef, useState } from "react";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";

type Option = { value: string; label: string; group?: string; disabled?: boolean };

export function SelectField({
  value, onChange, options, label, size = "default", fit = false, title, className = "", disabled = false, id: triggerId, describedBy, placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  options: Option[];
  label: string;
  /**
   * `small` is a toolbar chip: one fixed 32px row, no coarse-pointer growth.
   * `inline` drops the frame so a caller can group several into one control.
   * `field` matches the 36px text inputs of a form.
   */
  size?: "default" | "compact" | "small" | "inline" | "field";
  /** Size the trigger to its label instead of filling the container. */
  fit?: boolean;
  title?: string;
  className?: string;
  disabled?: boolean;
  /** For a `<label htmlFor>` naming the trigger, and the hint describing it. */
  id?: string;
  describedBy?: string;
  /** Trigger text while no option matches the value (loading, empty). */
  placeholder?: string;
}) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const id = useId();
  const compact = size !== "default" && size !== "field";
  const iconSize = size === "inline" ? "h-3 w-3" : compact ? "h-3.5 w-3.5" : "h-4 w-4";
  const triggerSize = size === "inline" ? "h-full rounded-[5px] px-2 text-[11px] font-medium"
    : size === "small" ? "h-8 px-2 text-xs"
    : size === "field" ? "h-9 px-2.5 text-sm"
    : compact ? "min-h-7 px-2 py-1 text-xs pointer-coarse:min-h-11" : "min-h-10 px-3 py-2 text-sm";
  const frame = size === "inline" ? "" : "rounded-md border border-[var(--color-border)] bg-[var(--color-surface)]";
  const setList = useCallback((node: HTMLDivElement | null) => {
    list.current = node;
    if (node) {
      const selected = node.querySelector<HTMLButtonElement>('[aria-selected="true"]:not(:disabled)');
      (selected ?? node.querySelector<HTMLButtonElement>('[role="option"]:not(:disabled)'))?.focus({ preventScroll: true });
    }
  }, []);
  const focusOption = (index: number) => {
    list.current?.querySelectorAll<HTMLButtonElement>('[role="option"]:not(:disabled)')[index]?.focus();
  };
  return (
    <div className={`min-w-0 ${fit ? "w-fit" : "w-full"} ${className}`}>
      <button type="button" ref={setAnchor} id={triggerId} aria-label={label} aria-describedby={describedBy} title={title} aria-haspopup="listbox"
        aria-expanded={open} aria-controls={open ? id : undefined} disabled={disabled}
        onClick={() => setOpen(!open)}
        onKeyDown={(event) => {
          if (["ArrowDown", "ArrowUp"].includes(event.key)) { event.preventDefault(); event.stopPropagation(); setOpen(true); }
        }}
        className={`flex w-full min-w-0 items-center justify-between gap-1 ${frame} text-left transition-colors hover:enabled:bg-[var(--color-surface-hover)] focus-visible:outline-2 focus-visible:outline-[var(--color-primary)] disabled:cursor-not-allowed disabled:opacity-50 ${triggerSize}`}>
        <span className="min-w-0 truncate">{options.find((option) => option.value === value)?.label ?? placeholder ?? label}</span>
        <ChevronDown className={`${iconSize} shrink-0 text-[var(--color-text-muted)]`} />
      </button>
      <AnchoredMenu anchor={anchor} open={open && !disabled} onClose={() => setOpen(false)} gap={4} matchAnchorWidth={fit || size === "inline" ? "min" : "exact"} minWidth={size === "inline" ? 96 : undefined} maxHeight={400}>
        <div id={id} role="listbox" aria-label={label} ref={setList} onKeyDown={(event) => {
          const rows = Array.from(list.current?.querySelectorAll<HTMLButtonElement>('[role="option"]:not(:disabled)') ?? []);
          const at = rows.indexOf(document.activeElement as HTMLButtonElement);
          const next = event.key === "Home" ? 0 : event.key === "End" ? rows.length - 1
            : event.key === "ArrowDown" ? Math.min(at + 1, rows.length - 1)
            : event.key === "ArrowUp" ? Math.max(at - 1, 0) : null;
          if (next !== null) { event.preventDefault(); event.stopPropagation(); focusOption(next); }
          if (event.key === "Escape") {
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
          }
          if (event.key === "Tab") setOpen(false);
        }}>
          {options.map((option, index) => (
            <div key={option.value}>
              {option.group && option.group !== options[index - 1]?.group && (
                <div className="px-3 pb-1 pt-3 text-xs text-[var(--color-text-muted)]">{option.group}</div>
              )}
              <button type="button" role="option" aria-selected={value === option.value} disabled={option.disabled} data-value={option.value}
                onClick={() => { onChange(option.value); setOpen(false); anchor?.focus({ preventScroll: true }); }}
                className={`flex w-full items-center gap-2 px-3 text-left hover:enabled:bg-[var(--color-surface-hover)] focus:bg-[var(--color-surface-hover)] focus:outline-none disabled:cursor-not-allowed disabled:text-[var(--color-text-muted)] ${compact ? "min-h-8 py-1.5 text-xs pointer-coarse:min-h-11" : "min-h-11 py-2 text-sm"}`}>
                <span className="min-w-0 flex-1 break-words">{option.label}</span>
                {value === option.value && <Check className={`${iconSize} shrink-0 text-[var(--color-primary)]`} />}
              </button>
            </div>
          ))}
        </div>
      </AnchoredMenu>
    </div>
  );
}
