import { AlertTriangle, Check, Copy } from "lucide-react";
import { useState } from "react";

import { IconButton } from "@/components/ui/IconButton";
import { errorStatus } from "@/lib/api";
import { formatUtc, parseIsoTime } from "@/lib/formatters";
import { copyText } from "@/lib/clipboard";

/**
 * Pieces shared by the panels that talk to a server's hummingbot-api: Settings →
 * Hummingbot API and the API card on Settings → Updates.
 */

/** 501: this server's API predates these routes. A newer build, not an outage. */
export const TOO_OLD = 501;

/**
 * Why a request failed, in the operator's terms.
 *
 * A 501 is the one failure that is not a failure: the server is healthy and this panel
 * is newer than it, so it gets a neutral notice naming the fix. Everything else is red.
 */
export function Problem({ error }: { error: unknown }) {
  const tooOld = errorStatus(error) === TOO_OLD;
  const message = error instanceof Error ? error.message : "Request failed";
  return (
    <div
      className={`flex items-start gap-2 rounded-md border p-3 text-xs ${
        tooOld
          ? "border-[var(--color-border)] text-[var(--color-text-muted)]"
          : "border-red-500/30 text-[var(--color-red)]"
      }`}
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{message}</span>
    </div>
  );
}

/**
 * An ISO timestamp as `YYYY-MM-DD HH:MM:SS UTC`, with the viewer's local time as the
 * tooltip. Unparseable input is shown as given.
 */
export function UtcTime({ value }: { value: string | null | undefined }) {
  const ms = parseIsoTime(value);
  if (ms === null) return <>{value || "unknown"}</>;
  return <span title={new Date(ms).toLocaleString()}>{formatUtc(value)}</span>;
}

/** A long identifier shown short, with the full value on hover and a copy button. */
export function Identifier({ value, length }: { value: string; length: number }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="inline-flex items-center gap-1">
      <span title={value}>{value.slice(0, length)}</span>
      <IconButton
        label={copied ? "Copied" : "Copy full value"}
        size="xs"
        onClick={async () => setCopied(await copyText(value))}
      >
        {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
      </IconButton>
    </span>
  );
}
