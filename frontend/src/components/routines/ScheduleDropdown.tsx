import { ChevronDown } from "lucide-react";
import { useState } from "react";
import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { ROUTINE_ACTION } from "@/components/routines/routineActionClass";

const PRESETS = [
  { label: "1m", sec: 60 },
  { label: "5m", sec: 300 },
  { label: "15m", sec: 900 },
  { label: "30m", sec: 1800 },
  { label: "1h", sec: 3600 },
  { label: "6h", sec: 21600 },
  { label: "1d", sec: 86400 },
  { label: "1w", sec: 604800 },
];

interface Props {
  onSchedule: (intervalSec: number) => void;
  disabled?: boolean;
}

export function ScheduleDropdown({ onSchedule, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLButtonElement | null>(null);

  return (
    <div className="relative">
      <button
        type="button"
        disabled={disabled}
        ref={setAnchor}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className={ROUTINE_ACTION}
      >
        Schedule
        <ChevronDown />
      </button>
      <AnchoredMenu anchor={anchor} open={open && !disabled} onClose={() => setOpen(false)} align="right" minWidth={160} maxHeight={352} role="menu" className="py-1">
          {PRESETS.map((p) => (
            <button
              key={p.sec}
              role="menuitem"
              type="button"
              onClick={() => {
                onSchedule(p.sec);
                setOpen(false);
              }}
              className="block min-h-8 w-full px-3 py-1.5 text-left text-xs text-[var(--color-text)] hover:bg-[var(--color-surface-hover)] focus-visible:bg-[var(--color-surface-hover)] pointer-coarse:min-h-11"
            >
              Every {p.label}
            </button>
          ))}
      </AnchoredMenu>
    </div>
  );
}
