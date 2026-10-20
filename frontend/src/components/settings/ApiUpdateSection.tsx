import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowUpCircle,
  CheckCircle2,
  Circle,
  ExternalLink,
  RefreshCw,
  XCircle,
  type LucideIcon,
} from "lucide-react";

import { Problem, TOO_OLD } from "@/components/settings/ApiServerParts";
import { SECTION_ACTION_CLASS, UpdatesSection } from "@/components/settings/UpdatesSection";
import { Card } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { useServer } from "@/hooks/useServer";
import { errorStatus } from "@/lib/api";
import { settingsApi, type ApiUpdateCheck, type ApiUpdateStatus } from "@/lib/api/settings";
import { formatUtc } from "@/lib/formatters";

/** Each verdict as a badge in the same place, and the same shape, as Condor's "Up to date". */
const UPDATE_STATUS: Record<ApiUpdateStatus, { label: string; tone: string; icon: LucideIcon }> = {
  up_to_date: { label: "Up to date", tone: "text-[var(--color-text-muted)]", icon: CheckCircle2 },
  update_available: { label: "Update available", tone: "text-[var(--color-primary)]", icon: ArrowUpCircle },
  ahead: { label: "Ahead of main", tone: "text-amber-500", icon: AlertTriangle },
  diverged: { label: "Diverged from main", tone: "text-amber-500", icon: AlertTriangle },
  unknown: { label: "Cannot compare", tone: "text-[var(--color-text-muted)]", icon: AlertTriangle },
  error: { label: "Check failed", tone: "text-[var(--color-red)]", icon: XCircle },
};

/** Verdicts whose badge already says everything; any other one shows the API's reason. */
const SELF_EXPLANATORY = new Set<ApiUpdateStatus>(["up_to_date", "update_available"]);

/** The manual update, run on the API host in its hummingbot-api checkout. */
const UPDATE_STEPS: { command?: string; text: string }[] = [
  { command: "make check-update", text: "See what changed upstream." },
  { command: "make update", text: "Pull the new source." },
  { text: "Back up the database and configuration as its maintenance guide describes." },
  { command: "make deploy", text: "Build the current source, then deploy it." },
];

const CODE = "rounded bg-[var(--color-bg)] px-1.5 py-0.5 font-mono text-[11px] text-[var(--color-text)]";

/** A revision the way Condor's card prints one: eight hex digits, the whole SHA on hover. */
function Revision({ value, className }: { value: string; className: string }) {
  return (
    <span className={`font-mono ${className}`} title={value}>
      {value.slice(0, 8)}
    </span>
  );
}

/**
 * Settings → Updates: the navbar-selected server's hummingbot-api source compared with
 * `main` (via the API's own `/system/update-check`), laid out like Condor's section above
 * it — the check where Condor has Refresh, the verdict where Condor says "Up to date".
 *
 * Condor only checks. Updating the API is a manual build and deploy on its host, so the
 * card keeps those steps behind "How to update" instead of offering an Update button.
 *
 * The check contacts GitHub, so it runs only when the button is pressed: the query is
 * disabled, which also keeps mounting, window focus and invalidation from firing it.
 * `server` is in the key, so a result — or a late answer — belongs to the server it was
 * asked about and never shows under another one.
 */
export function ApiUpdateSection() {
  const { server } = useServer();
  const check = useQuery({
    queryKey: ["api-update-check", server],
    queryFn: () => settingsApi.checkApiUpdate(server!),
    enabled: false,
    retry: false,
    gcTime: Infinity,
  });

  const unsupported = errorStatus(check.error) === TOO_OLD;
  const failed = (check.error != null && !unsupported) || check.data?.status === "error";
  const result = check.error == null ? check.data : undefined;

  return (
    <UpdatesSection
      title="Hummingbot API"
      description="Updated by hand on its own host; Condor only checks it against main."
      action={
        server && (
          <button
            onClick={() => void check.refetch()}
            disabled={check.isFetching || unsupported}
            className={SECTION_ACTION_CLASS}
          >
            {check.isFetching ? <Spinner size="xs" /> : <RefreshCw className="h-3.5 w-3.5" />}
            {failed ? "Retry check" : "Check"}
          </button>
        )
      }
    >
      <Card>
        <div className="flex items-start justify-between gap-3">
          <h3 className="text-sm font-semibold text-[var(--color-text)]" title={server ? `Server: ${server}` : undefined}>
            Hummingbot API
          </h3>
          <StatusBadge result={result} />
        </div>

        <div className="mt-3 space-y-2 text-xs">
          {!server ? (
            <p className="text-[var(--color-text-muted)]">Select a server in the top bar to check it.</p>
          ) : check.error != null ? (
            <Problem error={check.error} />
          ) : (
            result && <RevisionRow result={result} />
          )}

          {/* Collapsed like the "What's new" on Condor's card. */}
          <details>
            <summary className="cursor-pointer text-[var(--color-text-muted)] hover:text-[var(--color-text)]">
              How to update
            </summary>
            <p className="mt-2 text-[var(--color-text-muted)]">On the API host, in the hummingbot-api checkout:</p>
            <ol className="mt-2 space-y-1.5">
              {UPDATE_STEPS.map((step, i) => (
                <li key={i} className="flex items-baseline gap-2">
                  <span className="w-4 shrink-0 text-right tabular-nums text-[var(--color-text-muted)]">{i + 1}.</span>
                  <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
                    {step.command && <code className={CODE}>{step.command}</code>}
                    <span className="text-[var(--color-text-muted)]">{step.text}</span>
                  </span>
                </li>
              ))}
            </ol>
            <p className="mt-2 text-[var(--color-text-muted)]">Condor does not update, deploy or restart the API.</p>
          </details>
        </div>
      </Card>
    </UpdatesSection>
  );
}

/** The verdict, with when it was reached on hover. Before any check, "Not checked". */
function StatusBadge({ result }: { result: ApiUpdateCheck | undefined }) {
  const status = result ? (UPDATE_STATUS[result.status] ?? UPDATE_STATUS.unknown) : null;
  const Icon = status?.icon ?? Circle;
  return (
    <span
      className={`flex shrink-0 items-center gap-1.5 text-xs ${status?.tone ?? "text-[var(--color-text-muted)]"}`}
      title={result ? `Checked ${formatUtc(result.checked_at)}` : undefined}
    >
      <Icon className="h-3.5 w-3.5" />
      {status?.label ?? "Not checked"}
    </span>
  );
}

/** `repo  running → main`, the line Condor's card prints for its own checkout. */
function RevisionRow({ result }: { result: ApiUpdateCheck }) {
  const { current_revision: current, latest_revision: latest } = result;
  const differs = current != null && latest != null && current !== latest;
  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="w-12 shrink-0 uppercase tracking-wide text-[var(--color-text-muted)]">repo</span>
        {current ? (
          <Revision value={current} className="text-[var(--color-text)]" />
        ) : (
          <span className="text-[var(--color-text-muted)]">unknown</span>
        )}
        {differs && (
          <>
            <span className="text-[var(--color-text-muted)]">→</span>
            <Revision value={latest} className="text-[var(--color-primary)]" />
          </>
        )}
        {differs && result.compare_url && (
          <a
            href={result.compare_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-[var(--color-primary)] hover:underline"
          >
            Compare
            <ExternalLink className="h-3 w-3" />
          </a>
        )}
      </div>
      {result.reason && !SELF_EXPLANATORY.has(result.status) && (
        <p
          className={`mt-1 pl-14 ${
            result.status === "error" ? "text-[var(--color-red)]" : "text-[var(--color-text-muted)]"
          }`}
        >
          {result.reason}
        </p>
      )}
    </div>
  );
}
